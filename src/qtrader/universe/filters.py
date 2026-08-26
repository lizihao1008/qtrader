"""Time-aware tradability filters.

A symbol being *in* the universe is not the same as being tradable *right now*.
At each bar the filter asks four questions, all of them answerable from data at
or before that bar:

1. is there a price at all?
2. is the price above the penny-stock floor?
3. has it traded enough dollars recently to absorb an order?
4. has it printed at all in the last few minutes, or is the quote stale?

Question 4 is the fail-closed rule from CLAUDE.md §16 applied to research: a
strategy must not be credited with a fill on a symbol that has not traded.

The mask is used two ways: cross-sectional features rank only tradable symbols,
and the strategy may only hold tradable symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.panel import BarPanel


@dataclass(frozen=True)
class LiquidityFilter:
    """Per-bar tradability rules."""

    #: Minimum price; filters penny stocks where spreads dominate.
    min_price: float = 5.0

    #: Minimum trailing median dollar volume per bar.
    min_dollar_volume: float = 20_000.0

    #: Window for the dollar-volume median, in bars.
    lookback_bars: int = 60

    #: A symbol with no print in this many bars is treated as stale.
    max_stale_bars: int = 5

    def tradable(self, panel: BarPanel) -> pd.DataFrame:
        """Boolean ``timestamp x symbol`` mask of what may be traded per bar."""
        close = panel.close
        dollar_volume = close * panel.volume

        has_price = panel.available
        rich_enough = close >= self.min_price
        liquid_enough = (
            dollar_volume.rolling(self.lookback_bars, min_periods=self.lookback_bars)
            .median()
            .ge(self.min_dollar_volume)
        )
        fresh = (
            panel.traded.rolling(self.max_stale_bars, min_periods=1).sum().gt(0)
        )
        return has_price & rich_enough & liquid_enough & fresh
