"""Strategy interface.

A strategy answers exactly one question:

    *Given everything observable up to and including bar ``t``, what fraction of
    capital do I want in each symbol?*

It does **not** decide prices, share counts, costs or fills — that is the
engine's job. Keeping the two apart is what lets the same strategy object drive
a backtest and, later, live execution.

Timing contract
---------------
``StrategySignals.target_weights.loc[t]`` is decided from information available
at bar ``t``'s close and is executed by the engine on bar ``t+1``. Strategies
must never reference future rows; the delay is applied centrally in
:class:`qtrader.backtest.engine.BacktestEngine`, not here.

Weights
-------
A weight is a **signed fraction of deployed capital**: ``+0.25`` means a quarter
of the run's gross exposure held long in that symbol, ``-0.25`` the same held
short, ``0`` flat. Rows must satisfy ``sum(|w|) <= 1``; how much capital "1"
actually is comes from ``ExecutionConfig.gross_leverage``. A row of zeros is a
valid, explicit **no-trade** state (CLAUDE.md §11.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.panel import BarPanel
from ..universe.definition import Universe

#: Tolerance for the gross-weight check, to absorb floating-point noise.
WEIGHT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class MarketContext:
    """Everything a strategy is allowed to look at.

    Bundling these means the tradability mask is computed once, by the runner,
    and every strategy sees exactly the same view of what could be traded.
    """

    panel: BarPanel
    universe: Universe

    #: ``timestamp x symbol`` mask over tradable stocks (reference ETFs excluded).
    tradable: pd.DataFrame

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.panel.index

    @property
    def symbols(self) -> tuple[str, ...]:
        """Tradable universe members, in a stable order."""
        return tuple(self.tradable.columns)


@dataclass(frozen=True)
class StrategySignals:
    """What a strategy produces."""

    #: Target weight per symbol per bar; see the module docstring.
    target_weights: pd.DataFrame

    #: The alpha score the weights were derived from, for rank-IC diagnostics.
    #: ``None`` for strategies that do not rank (e.g. a single-symbol rule).
    scores: pd.DataFrame | None = None

    #: Per-symbol intermediate series, keyed by symbol, used by the price chart.
    indicators: dict[str, pd.DataFrame] = field(default_factory=dict)

    def __post_init__(self) -> None:
        weights = self.target_weights
        if not np.isfinite(weights.to_numpy()).all():
            raise ValueError("target_weights contains NaN or inf; use 0.0 for 'no position'")
        gross = weights.abs().sum(axis=1)
        if (gross > 1.0 + WEIGHT_TOLERANCE).any():
            worst = gross.idxmax()
            raise ValueError(
                f"target_weights must satisfy sum(|w|) <= 1; {gross.max():.4f} at {worst}"
            )

    @property
    def gross_weight(self) -> pd.Series:
        return self.target_weights.abs().sum(axis=1).rename("gross_weight")

    @property
    def net_weight(self) -> pd.Series:
        return self.target_weights.sum(axis=1).rename("net_weight")


class Strategy(ABC):
    """Base class for strategies that map market context to target weights."""

    #: Registry key, also used in run ids and report titles.
    name: str = "strategy"

    @abstractmethod
    def generate(self, context: MarketContext) -> StrategySignals:
        """Map observable market state to target weights."""

    def describe(self) -> dict:
        """Parameters of this instance, recorded in the run manifest."""
        return {"name": self.name, **{k: v for k, v in vars(self).items() if not k.startswith("_")}}
