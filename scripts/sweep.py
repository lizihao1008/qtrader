#!/usr/bin/env python3
"""Vary strategy parameters over the same data and compare the results.

    python scripts/sweep.py --config config/backtest/xsec_reversion.yaml \
        --grid rebalance_bars=5,15,30,60,120

Repeat --grid to cross several parameters. Results are printed and written to
results/<run_id>/sweep.csv. Treat the table as a sensitivity diagnostic: the
best row is not a strategy, it is the row that happened to fit this window.
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401  (sys.path side effect)

from qtrader.config import RunConfig
from qtrader.experiments import sweep


def parse_value(text: str):
    """Config values arrive as strings; keep ints as ints so they read cleanly."""
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    if text in ("true", "false"):
        return text == "true"
    return text


def parse_grid(entries: list[str]) -> dict[str, list]:
    grid = {}
    for entry in entries:
        if "=" not in entry:
            raise SystemExit(f"--grid expects name=v1,v2,...; got {entry!r}")
        name, values = entry.split("=", 1)
        grid[name] = [parse_value(v) for v in values.split(",")]
    return grid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--grid", action="append", required=True,
                        help="strategy parameter to vary, e.g. rebalance_bars=5,15,30")
    parser.add_argument("--ic-horizon", default="30b")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    grid = parse_grid(args.grid)

    table = sweep(config, grid, ic_horizon=args.ic_horizon)

    # Name the file after what was varied, so one sweep never overwrites another.
    out_dir = config.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sweep_{'_'.join(grid)}.csv"
    table.to_csv(out_path, index=False)

    with_pct = table.copy()
    for column in ("total_return", "max_drawdown", "hit_rate"):
        with_pct[column] = (with_pct[column] * 100).round(2)
    print(with_pct.round(4).to_string(index=False))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
