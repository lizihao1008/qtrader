#!/usr/bin/env python3
"""Run one hypothesis, attribute the result, and record it in the ledger.

    python scripts/search.py --config config/backtest/trend_ratchet_5min.yaml \
        --split m5_mine --label longer-hold --hypothesis "..." --set horizon_bars=24

Prints the headline statistics the loop is judged on — expectancy net of costs,
Sharpe, max drawdown, trades, hit rate, profit factor, long/short split — and
the arithmetic verdict on whether the shortfall is signal, holding period,
frequency or cost.

Every run is appended to results/search/ledger.jsonl, including the failures.
The count of trials against a window is the multiple-testing burden for anything
later found on it, so it has to be recorded, not remembered.
"""

from __future__ import annotations

import argparse

import numpy as np
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis import diagnose, diagnose_shortfall, performance_summary
from qtrader.experiments.ledger import DEFAULT_LEDGER, Trial, append_trial, trials_on


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--label", required=True, help="short name for this trial")
    parser.add_argument("--hypothesis", default="", help="what this change is expected to do")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    overrides = parse_assignments(args.set)
    diagnosis = diagnose(
        args.config, split=args.split, splits_file=args.splits_file, overrides=overrides
    )
    result = diagnosis.run.result
    episodes = diagnosis.episodes

    if not len(episodes):
        print(f"{args.label}: no completed round trips on {args.split}")
        append_trial(
            Trial(label=args.label, hypothesis=args.hypothesis, split=args.split,
                  overrides=overrides, metrics={"trades": 0}, verdict="no trades"),
            args.ledger,
        )
        return

    table = performance_summary(result, episodes)
    shortfall = diagnose_shortfall(episodes)
    metrics = result.metrics

    prior = trials_on(args.split, args.ledger)
    print(f"{args.label} on {args.split}  (trial {prior + 1} against this window)")
    if args.hypothesis:
        print(f"  hypothesis: {args.hypothesis}")
    if overrides:
        print(f"  change: {overrides}")
    print()
    if not args.quiet:
        print(table.round(3).to_string())
        print()
    print(
        f"  total return {metrics['total_return'] * 100:+.2f}%  ·  "
        f"Sharpe {metrics['sharpe']:+.2f}  ·  maxDD {metrics['max_drawdown'] * 100:.2f}%  ·  "
        f"turnover {metrics['daily_turnover']:.2f}x/day"
    )
    print("  " + shortfall.summary().replace("\n", "\n  "))

    append_trial(
        Trial(
            label=args.label,
            hypothesis=args.hypothesis,
            split=args.split,
            overrides=overrides,
            metrics={
                "trades": shortfall.trades,
                "expectancy_bps": round(shortfall.net_per_trade_bps, 3),
                "gross_bps": round(shortfall.gross_per_trade_bps, 3),
                "cost_bps": round(shortfall.cost_per_trade_bps, 3),
                "edge_t": round(float(shortfall.edge_t_stat), 3),
                "total_return": round(metrics["total_return"], 5),
                "sharpe": round(float(metrics["sharpe"]), 3) if np.isfinite(metrics["sharpe"]) else None,
                "max_drawdown": round(metrics["max_drawdown"], 5),
                "hit_rate": round(shortfall.hit_rate, 4),
                "profit_factor": round(float(table.loc["all", "profit_factor"]), 3),
                "turnover": round(float(metrics["daily_turnover"]), 3),
                "long_net": round(float(table.loc["long", "net_pnl"]), 1) if "long" in table.index else None,
                "short_net": round(float(table.loc["short", "net_pnl"]), 1) if "short" in table.index else None,
                "mean_hold_bars": round(float(table.loc["all", "mean_hold_bars"]), 1),
            },
            verdict=shortfall.verdict,
        ),
        args.ledger,
    )


if __name__ == "__main__":
    main()
