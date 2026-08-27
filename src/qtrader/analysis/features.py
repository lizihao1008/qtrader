"""Setup features: what the market looked like when a trade was decided.

Two sources, deliberately separated.

**Universal features** describe the market, not the strategy. Volatility,
relative volume, breadth, time of day, where the benchmark had been — any
strategy trading this universe can be screened against them, and they are
computed here once so no strategy has to reimplement them.

**Strategy features** are whatever the strategy itself considers a description
of its setup, supplied by :meth:`qtrader.strategies.base.Strategy.setup_features`.
A strategy that overrides nothing still gets the full universal screen; one that
opts in gets its own quantities screened too, with no changes here.

The scale rule
--------------
Every feature must be **comparable across symbols**: a z-score, a ratio, a
count, or a quantity in basis points. Raw price levels are not — a $600 stock's
moving average and a $30 stock's are different units, and pooling them produces
a screen that measures the universe's price distribution rather than the setup.
Everything below obeys this, and strategies are told to as well.

Causality
---------
All rolling windows look backwards only, so a value at bar ``t`` was knowable at
``t``. Episodes then read them at the *decision* bar, not the fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.sessions import session_date
from ..features.relative import align_reference, bar_log_returns, residual_returns, rolling_beta
from ..strategies.base import MarketContext, StrategySignals

BPS = 1e4

#: Windows used by the universal features, in bars.
VOL_WINDOW = 60
TREND_WINDOW = 30
VOLUME_WINDOW = 15
TYPICAL_VOLUME_WINDOW = 390
BETA_WINDOW = 60


@dataclass(frozen=True)
class FeatureSet:
    """Named context frames, ready to be sampled at a decision bar.

    ``per_symbol`` frames are ``timestamp x symbol``; ``shared`` series apply to
    the whole cross-section at that bar (breadth, market state, time of day).
    """

    per_symbol: dict[str, pd.DataFrame] = field(default_factory=dict)
    shared: dict[str, pd.Series] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.per_symbol) + tuple(self.shared)

    def merge(self, other: "FeatureSet", *, prefix: str = "") -> "FeatureSet":
        """Combine two sets, ``other`` winning any name it shares with ``self``.

        The displaced entry is kept under ``prefix + name`` rather than dropped:
        a strategy naming a feature the same as a universal one is expressing an
        opinion about that quantity, but the universal version is still what the
        other features are being compared against.
        """
        return FeatureSet(
            per_symbol=_merged(self.per_symbol, other.per_symbol, prefix),
            shared=_merged(self.shared, other.shared, prefix),
        )

    def sample(self, symbol: str, position: int) -> dict[str, float]:
        """Every feature's value for one symbol at one bar index."""
        row: dict[str, float] = {}
        for name, frame in self.per_symbol.items():
            row[name] = (
                frame.iat[position, frame.columns.get_loc(symbol)]
                if symbol in frame.columns
                else np.nan
            )
        for name, series in self.shared.items():
            row[name] = series.iat[position]
        return row


def _merged(mine: dict, theirs: dict, prefix: str) -> dict:
    merged = {
        (f"{prefix}{name}" if prefix and name in theirs else name): value
        for name, value in mine.items()
    }
    merged.update(theirs)
    return merged


def market_features(context: MarketContext) -> FeatureSet:
    """Strategy-agnostic description of the market at each bar."""
    panel = context.panel
    universe = context.universe
    symbols = list(context.symbols)

    close = panel.close
    returns = bar_log_returns(close)
    stock_returns = returns[symbols]

    benchmark_returns = returns[universe.benchmark]
    benchmark_frame = pd.DataFrame(
        {symbol: benchmark_returns for symbol in symbols}, index=panel.index
    )
    beta = rolling_beta(stock_returns, benchmark_frame, BETA_WINDOW)
    residual = residual_returns(stock_returns, benchmark_frame, beta)

    dollar_volume = close[symbols] * panel.volume[symbols]
    typical_volume = dollar_volume.rolling(
        TYPICAL_VOLUME_WINDOW, min_periods=VOL_WINDOW
    ).median()
    bar_range = (panel.field("high")[symbols] - panel.field("low")[symbols]) / close[symbols]

    trailing = stock_returns.rolling(TREND_WINDOW, min_periods=TREND_WINDOW).sum()
    eligible = context.tradable[symbols]
    day = session_date(panel.index).to_numpy()

    per_symbol = {
        "stock_vol_bps": stock_returns.rolling(VOL_WINDOW, min_periods=30).std() * BPS,
        "residual_vol_bps": residual.rolling(VOL_WINDOW, min_periods=30).std() * BPS,
        "beta_to_market": beta,
        "trailing_return_bps": trailing * BPS,
        "relative_volume": dollar_volume.rolling(VOLUME_WINDOW, min_periods=5).mean()
        / typical_volume.replace(0.0, np.nan),
        "bar_range_bps": bar_range.rolling(TREND_WINDOW, min_periods=10).mean() * BPS,
        "vwap_distance_bps": _vwap_distance(panel, symbols) * BPS,
    }
    sector_of = {
        s: universe.sectors[s]
        for s in symbols
        if universe.sectors.get(s) in returns.columns
    }
    if sector_of:
        sector_returns = align_reference(returns, sector_of)
        per_symbol["sector_return_bps"] = (
            sector_returns.rolling(TREND_WINDOW, min_periods=TREND_WINDOW).sum().reindex(
                columns=symbols
            )
            * BPS
        )

    shared = {
        "minute_of_session": pd.Series(day, index=panel.index).groupby(day).cumcount(),
        "n_eligible": eligible.sum(axis=1),
        "market_return_bps": benchmark_returns.rolling(
            TREND_WINDOW, min_periods=TREND_WINDOW
        ).sum() * BPS,
        "market_abs_return_bps": benchmark_returns.rolling(
            TREND_WINDOW, min_periods=TREND_WINDOW
        ).sum().abs() * BPS,
        "market_vol_bps": benchmark_returns.rolling(VOL_WINDOW, min_periods=30).std() * BPS,
        "return_dispersion_bps": trailing.where(eligible).std(axis=1) * BPS,
    }
    return FeatureSet(per_symbol=per_symbol, shared=shared)


def _vwap_distance(panel, symbols: list[str]) -> pd.DataFrame:
    """Distance from the session-to-date VWAP, as a fraction of price."""
    close = panel.close[symbols]
    typical = (panel.field("high")[symbols] + panel.field("low")[symbols] + close) / 3.0
    day = session_date(panel.index).to_numpy()
    volume = panel.volume[symbols]

    traded_value = (typical * volume).groupby(day).cumsum()
    traded_shares = volume.groupby(day).cumsum()
    vwap = traded_value / traded_shares.replace(0.0, np.nan)
    return close / vwap - 1.0


def strategy_features(strategy, signals: StrategySignals, context: MarketContext) -> FeatureSet:
    """Whatever the strategy declares about its own setup.

    Frames are reindexed onto the context's bars and symbols, so a strategy that
    returns a partial frame does not have to think about alignment.
    """
    declared = strategy.setup_features(signals, context)
    aligned = {
        name: frame.reindex(index=context.index, columns=list(context.symbols))
        for name, frame in declared.items()
    }
    return FeatureSet(per_symbol=aligned)


def setup_features(strategy, signals: StrategySignals, context: MarketContext) -> FeatureSet:
    """The full screen: universal market context plus the strategy's own view.

    On a name collision the strategy's version wins the plain name and the
    universal one is kept under a ``market_`` prefix, so nothing is silently
    lost.
    """
    return market_features(context).merge(
        strategy_features(strategy, signals, context), prefix="market_"
    )
