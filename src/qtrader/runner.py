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

    #: The LLM layer's report when one ran, else ``None``. Its presence is how
    #: downstream code tells Baseline Mode from LLM Enhanced Mode.
    validation: object | None = None


def build_context(
    config: RunConfig,
    store: BarStore | None = None,
    universe=None,
) -> MarketContext:
    """Load the universe's data and decide what was tradable at each bar.

    ``universe`` overrides the config's YAML so a lab can add one extra name
    without rewriting the file. Production runs omit it.
    """
    universe = universe if universe is not None else config.universe()
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

    fine_panel = None
    if config.data.fine_timeframe:
        fine_panel = load_panel(
            universe.all_symbols,
            timeframe=config.data.fine_timeframe,
            feed=config.data.feed,
            start=config.data.start_dt(),
            end=config.data.end_dt(),
            store=store or BarStore(config.data_root),
        )

    return MarketContext(
        panel=panel, universe=universe, tradable=tradable, fine_panel=fine_panel
    )


def execute(
    config: RunConfig,
    store: BarStore | None = None,
    context: MarketContext | None = None,
    validator=None,
) -> Run:
    """Run one configured backtest end to end.

    ``context`` may be supplied to reuse an already-loaded panel — a parameter
    sweep runs dozens of strategies over identical data and should read the
    parquet files once.

    ``validator`` is an optional callable ``(signals, context) -> report`` that
    may **narrow** the strategy's entries — the LLM layer (ADR-0007). Omitted,
    this function behaves exactly as it did before it existed, which is what
    makes the two modes comparable: Baseline is not a configuration of the
    enhanced path, it is the absence of the enhanced path.
    """
    context = context or build_context(config, store)
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    signals = strategy.generate(context)

    validation = None
    if validator is not None:
        validation = validator(signals, context)
        signals = validation.signals

    result = BacktestEngine(config.costs, config.execution).run(context, signals)
    if validation is not None:
        result.metrics["validation"] = validation.counts

    # Signal quality is evaluated separately from PnL: a strategy can be right
    # about the ranking and still lose money to costs, and the two failures need
    # different fixes.
    if signals.scores is not None:
        result.metrics["rank_ic"] = rank_ic_summary(
            signals.scores,
            context.panel.close[list(context.symbols)],
            context.tradable,
        )
    return Run(config=config, context=context, strategy=strategy, result=result,
               validation=validation)
