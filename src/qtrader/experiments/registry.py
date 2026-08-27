"""Persist a run so it can be reproduced and compared later.

Every run writes one directory::

    results/<run_id>/
        manifest.json     config + git commit + universe + data range + metrics
        metrics.json      headline statistics and signal diagnostics
        equity_curve.csv  per-bar equity, exposure and drawdown
        trades.csv        round trips
        fills.csv         individual executions
        weights.csv       target weights per bar (what the strategy asked for)
        report.html       charts: return curve, exposure, K-lines with buy/sell marks

CLAUDE.md §10: a result without this metadata is not a valid experiment.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from ..backtest.engine import BacktestResult
from ..config import RunConfig
from ..universe.definition import Universe
from ..viz.report import write_report


def git_commit() -> str | None:
    """Current commit hash, or ``None`` outside a Git working tree."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return out.stdout.strip() or None


def save_run(
    result: BacktestResult,
    config: RunConfig,
    *,
    universe: Universe,
    strategy_description: dict,
) -> Path:
    """Write all artifacts for ``result`` and return the run directory."""
    out_dir = config.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    result.equity_curve.to_csv(out_dir / "equity_curve.csv")
    result.trades.to_csv(out_dir / "trades.csv", index=False)
    result.fills.to_csv(out_dir / "fills.csv", index=False)
    # Only bars where the book changed; a full 1-minute grid would be mostly repeats.
    weights = result.signals.target_weights
    weights.loc[weights.ne(weights.shift()).any(axis=1)].to_csv(out_dir / "weights.csv")
    (out_dir / "metrics.json").write_text(json.dumps(result.metrics, indent=2, default=str))

    manifest = {
        "run_id": config.run_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "config": config.to_dict(),
        "universe": universe.to_dict(),
        "strategy": strategy_description,
        "data_range": {
            "start": str(result.panel.index.min()),
            "end": str(result.panel.index.max()),
            "n_bars": int(len(result.panel)),
            "n_symbols": len(result.panel.symbols),
        },
        "metrics": result.metrics,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    subtitle = (
        f"{config.strategy.name} {config.strategy.params} · "
        f"universe {universe.name} ({len(universe.symbols)} symbols) · "
        f"{config.data.timeframe} {config.data.feed} · "
        f"{result.panel.index.min()} → {result.panel.index.max()} · "
        f"costs: {config.costs.impact_bps:g} bps impact, "
        f"{config.costs.commission_per_share:g}/share"
    )
    write_report(
        result,
        out_dir / "report.html",
        subtitle=subtitle,
        symbols=config.report_symbols,
        max_candles=config.report_max_candles,
    )
    return out_dir
