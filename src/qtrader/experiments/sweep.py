"""Parameter sensitivity sweeps.

CLAUDE.md §22: a strategy is not promoted because of one attractive equity
curve. The first question after any result is how it behaves when a parameter
moves — a genuine edge degrades smoothly, an artifact of one lucky setting does
not.

A sweep re-runs the same configured strategy over the same data with one or more
parameters varied, and returns one row per combination. The market data is
loaded once and shared, so the cost is the strategy and engine only.

The result is a diagnostic, not a selection tool. Reading the best row off a
sweep and calling it "the strategy" is overfitting with extra steps; use the
shape of the surface, and confirm anything interesting on data the sweep never
saw.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pandas as pd

from ..config import RunConfig
from ..data.storage import BarStore
from ..runner import build_context, execute

#: Metrics reported for every combination, in column order.
SWEEP_METRICS = (
    "total_return",
    "sharpe",
    "max_drawdown",
    "n_trades",
    "daily_turnover",
    "gross_pnl",
    "total_costs",
    "net_pnl",
    "hit_rate",
)


def sweep(
    config: RunConfig,
    grid: dict[str, list],
    *,
    store: BarStore | None = None,
    ic_horizon: str = "30b",
) -> pd.DataFrame:
    """Run every combination in ``grid`` and collect one row of metrics each."""
    if not grid:
        raise ValueError("sweep needs at least one parameter to vary")

    context = build_context(config, store or BarStore(config.data_root))
    names = list(grid)
    rows = []

    for combination in itertools.product(*(grid[name] for name in names)):
        params = {**config.strategy.params, **dict(zip(names, combination))}
        variant = replace(config, strategy=replace(config.strategy, params=params))
        result = execute(variant, context=context).result

        row = dict(zip(names, combination))
        row.update({key: result.metrics.get(key) for key in SWEEP_METRICS})
        row["mean_ic"] = result.metrics.get("rank_ic", {}).get(ic_horizon, {}).get("mean_ic")
        rows.append(row)

    return pd.DataFrame(rows).sort_values(list(names)).reset_index(drop=True)
