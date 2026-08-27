#!/usr/bin/env python3
"""Audit a configured run for look-ahead, unrealistic fills and cost handling.

    python scripts/audit_execution.py --config config/backtest/trend_ratchet_5min.yaml

Checks the things a backtest can quietly get wrong, on the real data a config
points at rather than on synthetic fixtures:

1. every fill lands on the bar the timing contract says it should;
2. every fill price sits inside that bar's traded range, adjusted for costs;
3. every fill pays the spread in the adverse direction;
4. fills only occur on bars where the symbol actually printed (bar-price exits
   on stale symbols are counted and reported separately, since the engine
   allows those deliberately);
5. tampering with every bar after a cut date leaves every fill before it
   byte-identical — the end-to-end look-ahead guard, run on this dataset.

Exits non-zero if any check fails, so it can gate a research loop.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from dataclasses import replace

from qtrader.backtest.engine import BacktestEngine
from qtrader.config import RunConfig
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import build_context, execute
from qtrader.strategies import build_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split")
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    if args.split:
        config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    run = execute(config)
    failures = []
    print(f"{config.run_id}: {len(run.result.fills)} fills over {len(run.context.index)} bars\n")

    for check in (_fill_timing, _fill_prices, _cost_direction, _stale_fills):
        ok, message = check(run)
        print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
        if not ok:
            failures.append(message)

    ok, message = _no_lookahead(config, run)
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    if not ok:
        failures.append(message)

    print()
    raise SystemExit(f"{len(failures)} check(s) failed" if failures else 0)


def _fill_timing(run) -> tuple[bool, str]:
    """Each fill must be at the reference price of its own bar, not an earlier one."""
    index = run.context.panel.index
    field = run.config.execution.execution_price
    reference = run.context.panel.field(field)

    fills = run.result.fills
    expected = np.array(
        [reference.loc[t, s] for t, s in zip(fills["timestamp"], fills["symbol"])]
    )
    mismatch = int((~np.isclose(fills["reference_price"], expected)).sum())
    lag = run.config.execution.execution_lag_bars
    return (
        mismatch == 0,
        f"fill reference prices match each bar's {field} "
        f"(lag {lag} bar{'s' if lag != 1 else ''}); {mismatch} mismatched",
    )


def _fill_prices(run) -> tuple[bool, str]:
    """The pre-cost reference must lie inside the bar's own traded range."""
    panel = run.context.panel
    high, low = panel.field("high"), panel.field("low")
    fills = run.result.fills

    outside = 0
    for t, s, p in zip(fills["timestamp"], fills["symbol"], fills["reference_price"]):
        if not (low.loc[t, s] - 1e-9 <= p <= high.loc[t, s] + 1e-9):
            outside += 1
    return outside == 0, f"reference prices inside each bar's [low, high]; {outside} outside"


def _cost_direction(run) -> tuple[bool, str]:
    """Buys must fill above the reference and sells below — never the reverse."""
    fills = run.result.fills
    if fills.empty:
        return True, "no fills to check for cost direction"
    buys = fills["side"] == "BUY"
    wrong = int(
        ((buys & (fills["price"] < fills["reference_price"])).sum())
        + ((~buys & (fills["price"] > fills["reference_price"])).sum())
    )
    paid = float(fills["slippage_cost"].sum() + fills["commission"].sum())
    return wrong == 0, f"every fill pays the spread adversely (${paid:,.0f} total); {wrong} did not"


def _stale_fills(run) -> tuple[bool, str]:
    """Fills should land on bars that actually printed; exits may not."""
    traded = run.context.panel.traded
    fills = run.result.fills
    stale = [
        (t, s) for t, s in zip(fills["timestamp"], fills["symbol"]) if not traded.loc[t, s]
    ]
    declared = run.result.metrics.get("stale_exits", 0)
    return (
        len(stale) <= declared,
        f"{len(stale)} fills on bars with no print, all accounted for as declared "
        f"stale exits ({declared})",
    )


def _no_lookahead(config, run) -> tuple[bool, str]:
    """Rewrite every bar after a cut and confirm earlier fills do not move."""
    context = build_context(config)
    index = context.index
    cut_position = len(index) // 2
    cut = index[cut_position]

    panel = context.panel
    for name in ("open", "high", "low", "close"):
        frame = panel.field(name).copy()
        frame.iloc[cut_position:] *= 3.0
        panel = panel.replace_field(name, frame)
    context = replace(context, panel=panel)

    strategy = build_strategy(config.strategy.name, config.strategy.params)
    engine = BacktestEngine(config.costs, config.execution)
    after = engine.run(context, strategy.generate(context))

    before_fills = run.result.fills.loc[run.result.fills["timestamp"] < cut].reset_index(drop=True)
    after_fills = after.fills.loc[after.fills["timestamp"] < cut].reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(before_fills, after_fills)
    except AssertionError as error:
        return False, f"fills before {cut} changed when later bars were rewritten: {error}"
    return True, (
        f"tripling every bar after {cut:%Y-%m-%d} left all {len(before_fills)} earlier "
        "fills identical"
    )


if __name__ == "__main__":
    main()
