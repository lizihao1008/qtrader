#!/usr/bin/env python3
"""Does the confirmation score predict the candidate's forward return?

    python scripts/analyze_confirmation.py --config config/backtest/sr_momentum_5min.yaml \
        --split m5_mine --scores results/sr_momentum_5min__m5_mine/kronos_scores.parquet

This is the direct test, and it is cheaper and cleaner than a backtest: it asks
whether the external model has any information about what happens after the
setups the strategy proposed, before position limits, sizing and exits get in
the way. A filter that fails here cannot help a backtest except by luck.

The forward return is measured from the price the strategy would actually pay
(the next bar's open) to the close `horizon` bars later, and it is signed by the
direction the strategy proposed, so a positive number is a profitable trade.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.config import RunConfig
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.models.kronos_confirm import load_scores
from qtrader.runner import build_context
from qtrader.strategies import build_strategy


def summarise(name: str, score: np.ndarray, forward: np.ndarray, cost_bps: float) -> None:
    n = len(forward)
    if n < 30:
        print(f"  {name:<22} {n:>6} candidates — too few to say anything")
        return
    hit = (forward > 0).mean()
    mean = forward.mean() * 1e4
    t_stat = mean / (forward.std(ddof=1) * 1e4 / np.sqrt(n))
    agree = np.sign(score) == np.sign(forward)
    print(
        f"  {name:<22} {n:>6}  hit {hit:6.1%}  gross {mean:+7.2f} bps "
        f"(t={t_stat:+5.2f})  net {mean - cost_bps:+7.2f}  score-agrees {agree.mean():6.1%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--scores", required=True)
    parser.add_argument("--horizon", type=int, help="defaults to the strategy's horizon")
    parser.add_argument("--cost-bps", type=float, default=3.0)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    context = build_context(config)
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    signals = strategy.generate(context)
    horizon = args.horizon or config.strategy.params.get("horizon_bars", 12)

    scores = load_scores(args.scores).reindex(
        index=context.index, columns=list(context.symbols)
    )
    opens = context.panel.field("open")[list(context.symbols)]
    close = context.panel.close[list(context.symbols)]
    # entry at the next bar's open, exit at the close `horizon` bars after that
    entry = opens.shift(-1)
    exit_ = close.shift(-1 - horizon)
    forward = np.log(exit_ / entry)

    rows = []
    for symbol, frame in signals.indicators.items():
        fired = frame["candidate"]
        for timestamp, direction in fired[fired != 0].items():
            rows.append(
                {
                    "symbol": symbol,
                    "direction": int(np.sign(direction)),
                    "score": scores.at[timestamp, symbol],
                    "forward": forward.at[timestamp, symbol] * np.sign(direction),
                }
            )
    table = pd.DataFrame(rows).dropna()
    if table.empty:
        raise SystemExit("no scored candidates with a realised forward return")

    print(f"\n{config.run_id} · {args.split} · horizon {horizon} bars · "
          f"{len(table):,} scored candidates\n")
    print(f"  {'group':<22} {'n':>6}  {'hit':>10}  {'gross':>17}  {'net':>11}  {'agree':>13}")
    summarise("all candidates", table["score"].to_numpy(), table["forward"].to_numpy(),
              args.cost_bps)
    for direction, label in ((1, "long candidates"), (-1, "short candidates")):
        side = table[table["direction"] == direction]
        summarise(label, side["score"].to_numpy(), side["forward"].to_numpy(), args.cost_bps)

    print("\n  confirmation threshold sweep (score must agree with the direction):\n")
    print(f"  {'min |score|':<22} {'n':>6}  {'hit':>10}  {'gross':>17}  {'net':>11}  {'kept':>13}")
    signed = table["score"] * table["direction"]
    for threshold in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0):
        kept = table[signed >= threshold]
        if len(kept) < 30:
            print(f"  {threshold:<22} {len(kept):>6} — too few")
            continue
        summarise(f"{threshold}", kept["score"].to_numpy(), kept["forward"].to_numpy(),
                  args.cost_bps)
        print(f"  {'':<22} {'':>6}  keeps {len(kept)/len(table):5.1%} of candidates")

    # Spearman by hand: pandas routes "spearman" through scipy, which this
    # environment does not carry.
    ic = table["score"].rank().corr(table["forward"].rank())
    n = len(table)
    print(f"\n  rank IC of score vs realised return: {ic:+.4f} "
          f"(t={ic*np.sqrt(n-2)/np.sqrt(max(1-ic**2, 1e-12)):+.2f}, n={n:,})")
    print("  a filter with no rank IC here cannot improve the backtest except by luck.\n")


if __name__ == "__main__":
    main()
