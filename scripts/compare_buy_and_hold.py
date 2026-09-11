#!/usr/bin/env python3
"""What did the strategy earn, against simply holding the same instruments?

    python scripts/compare_buy_and_hold.py \
        --config config/backtest/sr_momentum_index_5min.yaml --split last_year

The comparison is not as simple as two total returns, because the two things do
not carry the same risk. This strategy is flat by 15:50 every day, so it never
holds overnight; a buy-and-hold position holds continuously. For equity indices
that difference is most of the question, so the benchmark return is split into
the part earned while the market is open and the part earned between the close
and the next open — the second of which an intraday strategy structurally cannot
compete for.

Exposure-adjusted figures are reported alongside the raw ones: a strategy that
is in the market a fifth of the time and earns a fifth of the return has matched
buy-and-hold per unit of exposure, not lost to it.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.config import RunConfig
from qtrader.data.sessions import session_date
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import execute

BPS = 1e4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    run = execute(config)
    curve = run.result.equity_curve
    symbols = list(run.context.symbols)
    panel = run.context.panel

    start, end = curve.index[0], curve.index[-1]
    years = (end - start).days / 365.25
    strat = curve["equity"].iloc[-1] / curve["equity"].iloc[0] - 1.0

    print(f"\n{config.run_id} · {args.split} · "
          f"{start.date()} to {end.date()} ({years:.2f} years)\n")

    rows = [_line("strategy (intraday only)", strat, curve["equity"], years)]
    holds = {}
    for symbol in symbols:
        close = panel.close[symbol].ffill().dropna()
        holds[symbol] = close
        rows.append(_line(f"hold {symbol}", close.iloc[-1] / close.iloc[0] - 1.0,
                          close, years))
    if len(symbols) > 1:
        blend = sum(holds[s] / holds[s].iloc[0] for s in symbols) / len(symbols)
        rows.append(_line(f"hold {'/'.join(symbols)} equally",
                          blend.iloc[-1] / blend.iloc[0] - 1.0, blend, years))

    table = pd.DataFrame(rows).set_index("what")
    print(table.to_string())

    _exposure(run, curve, strat, years)
    _session_split(panel, symbols)


def _line(name: str, total: float, series: pd.Series, years: float) -> dict:
    returns = series.pct_change().dropna()
    # 78 five-minute bars a session; annualise on trading time, as metrics.py does
    periods = 78 * 252
    sharpe = (returns.mean() / returns.std() * np.sqrt(periods)) if returns.std() else np.nan
    peak = series.cummax()
    return {
        "what": name,
        "total return": f"{total:+.2%}",
        "annualised": f"{(1 + total) ** (1 / years) - 1:+.2%}",
        "Sharpe": f"{sharpe:+.2f}",
        "max drawdown": f"{(series / peak - 1.0).min():.2%}",
    }


def _exposure(run, curve, strat: float, years: float) -> None:
    """How much of the time the strategy actually had money at risk."""
    gross = curve["gross_exposure"] / curve["equity"]
    invested = (gross > 1e-9).mean()
    average = gross.mean()
    print(f"\n  time with a position open : {invested:.1%} of bars")
    print(f"  average gross exposure    : {average:.1%} of equity")
    if average > 0:
        print(f"  return per unit of exposure: {strat / average:+.2%} "
              f"(strategy {strat:+.2%} at {average:.1%} average exposure)")


def _session_split(panel, symbols: list[str]) -> None:
    """Split each hold into the part earned intraday and the part earned overnight.

    An intraday strategy is flat by the close, so the overnight component is not
    a return it under-performed — it is one it never competed for.
    """
    print("\n  where a buy-and-hold return actually comes from:\n")
    print(f"  {'symbol':>7} {'total':>9} {'intraday':>10} {'overnight':>10}")
    for symbol in symbols:
        close = panel.close[symbol].ffill()
        opens = panel.field("open")[symbol].ffill()
        day = session_date(close.index).to_numpy()
        frame = pd.DataFrame({"open": opens, "close": close, "day": day}).dropna()
        first = frame.groupby("day")["open"].first()
        last = frame.groupby("day")["close"].last()

        intraday = np.log(last / first).sum()
        overnight = np.log(first.shift(-1).dropna() / last.iloc[:-1]).sum()
        total = np.log(last.iloc[-1] / first.iloc[0])
        print(f"  {symbol:>7} {np.expm1(total):>+9.2%} {np.expm1(intraday):>+10.2%} "
              f"{np.expm1(overnight):>+10.2%}")


if __name__ == "__main__":
    main()
