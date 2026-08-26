"""Assemble and execute a configured run.

This is the one place that knows the order of the pipeline::

    config -> universe -> panel -> tradability mask -> MarketContext
           -> strategy -> engine -> BacktestResult

Scripts and tests both go through here, so there is a single definition of what
"running a backtest" means, and no way for the two to drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backtest.engine import BacktestEngine, BacktestResult
from .backtest.signal_metrics import rank_ic_summary
from .config import RunConfig
from .data.ingest import load_panel
from .data.storage import BarStore
from .strategies import MarketContext, Strategy, build_strategy


@dataclass
class Run:
    """A completed run and the objects that produced it."""

    config: RunConfig
    context: MarketContext
    strategy: Strategy
    result: BacktestResult


def build_context(config: RunConfig, store: BarStore | None = None) -> MarketContext:
    """Load the universe's data and decide what was tradable at each bar."""
    universe = config.universe()
    panel = load_panel(
        universe.all_symbols,
        timeframe=config.data.timeframe,
        feed=config.data.feed,
        start=config.data.start_dt(),
        end=config.data.end_dt(),
        store=store or BarStore(config.data_root),
    )
    # Reference ETFs are data, never positions: the mask covers stocks only.
    tradable = config.liquidity.tradable(panel)[list(universe.symbols)]
    return MarketContext(panel=panel, universe=universe, tradable=tradable)


def execute(
    config: RunConfig,
    store: BarStore | None = None,
    context: MarketContext | None = None,
) -> Run:
    """Run one configured backtest end to end.

    ``context`` may be supplied to reuse an already-loaded panel — a parameter
    sweep runs dozens of strategies over identical data and should read the
    parquet files once.
    """
    context = context or build_context(config, store)
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    signals = strategy.generate(context)
    result = BacktestEngine(config.costs, config.execution).run(context, signals)

    # Signal quality is evaluated separately from PnL: a strategy can be right
    # about the ranking and still lose money to costs, and the two failures need
    # different fixes.
    if signals.scores is not None:
        result.metrics["rank_ic"] = rank_ic_summary(
            signals.scores,
            context.panel.close[list(context.symbols)],
            context.tradable,
        )
    return Run(config=config, context=context, strategy=strategy, result=result)
