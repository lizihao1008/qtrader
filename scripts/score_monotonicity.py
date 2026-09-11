#!/usr/bin/env python3
"""Does the score predict, and monotonically?

    python scripts/score_monotonicity.py \
        --config config/backtest/xsec_momentum_1min.yaml --split m5_mine

Buckets every (bar, symbol) score and reports the mean forward return at 5, 15
and 30 minutes. This is the question the backtest cannot answer on its own: a
P&L number mixes the score with the exits, the book, the costs and the clock,
and a rule can lose money on a good score or make it on a bad one.

Read the gradient, not the best bucket. A usable score is ordered across the
buckets; one extreme bucket with flat neighbours is a handful of observations,
and a U-shape means the score measures magnitude rather than direction.

Read the **tradeable** rows, not the `fwd` ones. `fwd` starts at the decision
bar's close; a fill starts at the next bar's open, and whatever moves in between
is real and unreachable (R31).
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis.score_monotonicity import (
    DEFAULT_HORIZONS,
    monotonicity,
    score_buckets,
)
from qtrader.config import RunConfig
from qtrader.data.storage import BarStore
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import build_context, execute


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--buckets", type=int, default=10)
    parser.add_argument("--horizons", type=int, nargs="*", default=list(DEFAULT_HORIZONS))
    parser.add_argument("--per-factor", action="store_true",
                        help="also bucket each factor's own z-score")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    context = build_context(config, BarStore(config.data_root))
    run = execute(config, context=context)
    if run.result.signals.scores is None:
        raise SystemExit(f"{config.strategy.name} publishes no score to bucket")

    symbols = list(context.symbols)
    close = context.panel.close[symbols]
    eligible = context.tradable[symbols]
    pd.set_option("display.width", 220)

    def report(name: str, score: pd.DataFrame) -> None:
        table = score_buckets(
            score, close, eligible=eligible,
            horizons=args.horizons, n_buckets=args.buckets,
            open_=context.panel.field("open")[symbols],
            entry_lag=int(config.execution.execution_lag_bars),
        )
        if table.empty:
            print(f"\n{name}: too few distinct values to bucket")
            return
        print(f"\n=== {name} ===")
        print(table.to_string(float_format=lambda v: f"{v:,.3f}"))
        for prefix, label in (("fwd", "from the decision close (what the score saw)"),
                              ("tradeable", "from the next open (what a fill reaches)")):
            rows = [monotonicity(table, h, prefix=prefix) for h in args.horizons]
            rows = [r for r in rows if r]
            if not rows:
                continue
            print(f"  {label}")
            print(pd.DataFrame(rows).set_index("horizon")
                  .to_string(float_format=lambda v: f"{v:,.3f}"))

    print(f"{config.run_id} · {args.split} · {len(context.index):,} bars "
          f"x {len(symbols)} symbols · {args.buckets} buckets")
    report("score", run.result.signals.scores)

    if args.per_factor:
        # A composite can be flat because its parts cancel. Reporting each part
        # separately is the only way to tell that apart from all parts being
        # uninformative, and they call for different next steps.
        for column in sorted(
            c for c in next(iter(run.result.signals.indicators.values()))
            if c.startswith("z_")
        ):
            report(column, run.result.signals.stack(column))


if __name__ == "__main__":
    sys.exit(main())
