"""Moving-average crossover — the single-symbol deterministic baseline.

Rule, evaluated at each bar close, independently per symbol:

    fast SMA > slow SMA  -> hold long   (+w)
    fast SMA < slow SMA  -> hold short  (-w) if shorting is enabled, else flat
    warm-up / equal      -> flat        ( 0)

Capital is split equally across the universe's symbols, so on a one-symbol
universe this is exactly the original baseline: fully invested or flat.

Intraday hygiene: positions are forced flat from ``flat_time`` (exchange local)
onwards, so the strategy never carries overnight risk it has not modelled. The
cutoff is clock-based and therefore causal.

This strategy exists to validate the data path, the engine and the reporting —
not because MA crossovers are expected to be profitable after costs.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..data.sessions import at_or_after_market_time
from ..features.stock import macd, sma
from .base import MarketContext, Strategy, StrategySignals


class MACrossStrategy(Strategy):
    name = "ma_cross"

    def __init__(
        self,
        fast: int = 20,
        slow: int = 60,
        *,
        allow_short: bool = False,
        flat_time: str | None = "15:55",
    ):
        if fast >= slow:
            raise ValueError(f"fast window ({fast}) must be shorter than slow ({slow})")
        self.fast = int(fast)
        self.slow = int(slow)
        self.allow_short = bool(allow_short)
        self.flat_time = flat_time

    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        close = context.panel.close[symbols]

        fast_ma = close.rolling(self.fast, min_periods=self.fast).mean()
        slow_ma = close.rolling(self.slow, min_periods=self.slow).mean()

        direction = pd.DataFrame(0.0, index=close.index, columns=symbols)
        direction[fast_ma > slow_ma] = 1.0
        if self.allow_short:
            direction[fast_ma < slow_ma] = -1.0
        direction[fast_ma.isna() | slow_ma.isna()] = 0.0

        # Only hold what could actually be traded at this bar.
        direction = direction.where(context.tradable[symbols], 0.0)

        if self.flat_time is not None:
            cutoff = dt.time.fromisoformat(self.flat_time)
            direction.loc[at_or_after_market_time(close.index, cutoff).to_numpy()] = 0.0

        weights = direction / len(symbols)
        indicators = {
            symbol: pd.concat(
                [
                    fast_ma[symbol].rename(f"sma_{self.fast}"),
                    slow_ma[symbol].rename(f"sma_{self.slow}"),
                    macd(close[symbol]),
                ],
                axis=1,
            )
            for symbol in symbols
        }
        return StrategySignals(target_weights=weights, indicators=indicators)
