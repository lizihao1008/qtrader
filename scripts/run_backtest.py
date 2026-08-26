#!/usr/bin/env python3
"""Run one configured strategy end to end and write its report.

    python scripts/run_backtest.py --config config/backtest/xsec_reversion.yaml

Pipeline: clean bars -> panel -> tradability mask -> strategy weights ->
cost-aware engine -> metrics -> results/<run_id>/.
"""

from __future__ import annotations

import argparse
import webbrowser

import _bootstrap  # noqa: F401  (sys.path side effect)

from qtrader.config import RunConfig
from qtrader.experiments import save_run
from qtrader.runner import execute

_HEADLINE = (
    "total_return", "benchmark_return", "sharpe", "max_drawdown",
    "n_trades", "hit_rate", "daily_turnover", "gross_pnl", "total_costs", "net_pnl",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--open", action="store_true", help="open the report in a browser")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    run = execute(config)
    result = run.result

    out_dir = save_run(
        result,
        config,
        universe=run.context.universe,
        strategy_description=run.strategy.describe(),
    )

    print(
        f"{config.run_id}: {len(result.panel)} bars x "
        f"{len(run.context.symbols)} symbols, {result.metrics['n_trades']} trades"
    )
    for key in _HEADLINE:
        print(f"  {key:<18} {result.metrics[key]}")
    for horizon, stats in result.metrics.get("rank_ic", {}).items():
        if stats.get("n_obs"):
            print(
                f"  rank_ic[{horizon}]      mean={stats['mean_ic']:+.5f} "
                f"t={stats['t_stat']:+.1f} pos={stats['share_positive']:.1%}"
            )
    print(f"report: {out_dir / 'report.html'}")

    if args.open:
        webbrowser.open((out_dir / "report.html").resolve().as_uri())


if __name__ == "__main__":
    main()
