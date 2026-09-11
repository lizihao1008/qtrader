#!/usr/bin/env python3
"""Do not trade inside a range: gate sr_momentum on market_state's detector.

    python scripts/run_consolidation_gate.py \
        --config config/backtest/sr_momentum_index_5min.yaml --split last_year

Runs the same strategy twice over identical bars — once as configured, once with
new entries vetoed while the consolidation detector says the symbol is inside a
range — and reports both. The panel is loaded once and shared, so the only
difference between the two arms is the gate.

The mask is always computed on 5-minute bars, the grid the detector's thresholds
were chosen on. On a finer decision grid it is carried down causally; see
`qtrader.experiments.consolidation_gate`.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis.episodes import extract_episodes
from qtrader.config import RunConfig
from qtrader.data.storage import BarStore
from qtrader.experiments.consolidation_gate import DEFAULT_CONFIG, in_range_mask
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import build_context, execute

BPS = 1e4


def _mask_for(config: RunConfig, context, detector_config: str) -> pd.DataFrame:
    """The veto on the decision grid, whatever timeframe that is."""
    symbols = list(context.symbols)
    if config.data.timeframe == "5Min":
        return in_range_mask(context.panel, symbols, config_path=detector_config)
    if context.fine_panel is None or config.data.fine_timeframe != "5Min":
        raise SystemExit(
            f"decision grid is {config.data.timeframe}; the detector needs the "
            "5-minute series, so set data.fine_timeframe: 5Min"
        )
    return in_range_mask(
        context.fine_panel, symbols,
        fine_index=context.index, config_path=detector_config,
    )


def _describe(run, label: str) -> dict:
    episodes = extract_episodes(run, context_bars=1)
    metrics = run.result.metrics
    row = {
        "arm": label,
        "total_return": float(metrics["total_return"]),
        "n_trades": int(metrics["n_trades"]),
        "hit_rate": float(metrics["hit_rate"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "time_in_market": float(metrics["time_in_market"]),
    }
    if len(episodes):
        features = episodes.features
        weights = features["notional"].to_numpy(float)
        row["gross_bps_weighted"] = float(
            np.average(features["gross_return_bps"], weights=weights))
        row["net_bps_weighted"] = float(
            np.average(features["net_return_bps"], weights=weights))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--detector-config", default=DEFAULT_CONFIG)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    store = BarStore(config.data_root)
    context = build_context(config, store)
    mask = _mask_for(config, context, args.detector_config)

    coverage = mask.reindex(index=context.index, columns=list(context.symbols))
    coverage = coverage.fillna(False)
    print(f"{config.run_id} · {args.split} · {len(context.index):,} bars")
    print("in-range share: " + ", ".join(
        f"{symbol} {coverage[symbol].mean():.1%}" for symbol in coverage))

    baseline = execute(config, context=context)
    rows = [
        _describe(baseline, "baseline"),
        _describe(
            execute(config.with_overrides({"entry_veto": mask}), context=context),
            "no entry in range",
        ),
        # The complement is the control. If the detector separates good trades
        # from bad, the two gates must move gross in opposite directions; if
        # both merely trade less, it is sorting on nothing.
        _describe(
            execute(config.with_overrides({"entry_veto": ~coverage}), context=context),
            "only entry in range",
        ),
    ]
    table = pd.DataFrame(rows).set_index("arm")
    pd.set_option("display.width", 200)
    print()
    print(table.to_string(float_format=lambda v: f"{v:,.4f}"))
    print()
    _split_baseline(baseline, coverage)


def _split_baseline(run, coverage: pd.DataFrame) -> None:
    """Score the baseline's own trades by the state at their decision bar.

    Cleaner than comparing the two gated arms: gating changes which trades
    happen at all (a refused entry frees the book for a later one), so the arms
    do not share a trade population. Here every trade is the same trade; only
    the label differs.
    """
    episodes = extract_episodes(run, context_bars=1)
    if not len(episodes):
        print("no round trips to split")
        return
    features = episodes.features.copy()
    lag = int(run.config.execution.execution_lag_bars)
    index = run.context.index
    decided, flags = [], []
    for _, trade in features.iterrows():
        position = index.get_indexer([pd.Timestamp(trade["entry_time"])])[0]
        decision = position - lag
        ok = position >= 0 and decision >= 0 and trade["symbol"] in coverage
        decided.append(ok)
        flags.append(bool(coverage[trade["symbol"]].iloc[decision]) if ok else False)
    features = features.loc[np.asarray(decided)]
    features["in_range"] = np.asarray(flags)[np.asarray(decided)]

    print("baseline trades, labelled by the detector state at the DECISION bar:")
    out = []
    for label, subset in features.groupby("in_range"):
        weights = subset["notional"].to_numpy(float)
        gross = subset["gross_return_bps"].to_numpy(float)
        out.append({
            "decision bar": "inside a range" if label else "outside",
            "trades": len(subset),
            "gross_bps_weighted": float(np.average(gross, weights=weights)),
            "gross_bps_mean": float(gross.mean()),
            "t_stat": float(gross.mean() / (gross.std(ddof=1) / np.sqrt(len(gross)))),
            "hit_rate": float(subset["gross_win"].mean()),
            "hold_bars": float(subset["hold_bars"].mean()),
        })
    print(pd.DataFrame(out).set_index("decision bar").to_string(
        float_format=lambda v: f"{v:,.3f}"))


if __name__ == "__main__":
    sys.exit(main())
