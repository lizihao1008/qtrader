"""Cross-sectional peer-relative strategy — the project's first real alpha rule.

This is the thesis of the whole system in its simplest honest form:

1. strip the market/sector move out of every stock's return (residual return);
2. accumulate that residual over a short lookback;
3. standardise it **across stocks at the same timestamp**, so the question is
   comparative — who is the biggest outlier right now, not who went up;
4. hold the extremes: long the bottom of the distribution and short the top
   (``reversion``), or the reverse (``momentum``);
5. hold nothing at all when the cross-section has no meaningful outliers.

Why residuals. A stock up 0.4% while its sector is up 0.5% has not gone up. The
sector ETF absorbs most of the common factor, and a trailing beta absorbs the
rest, so what is left is closer to stock-specific information.

Why a threshold. ``min_abs_zscore`` gives the strategy an explicit **no-trade**
state (CLAUDE.md §11.5): on a quiet cross-section with no dispersion the right
answer is an empty book, not the least-bad name available.

Why a rebalance interval. A 1-minute cross-section changes every bar. Acting on
every change would pay spread on every bar and turn a small edge into a
guaranteed cost bill; the book is refreshed on a fixed grid instead, anchored to
the session open.

Both ``mode`` values are exposed deliberately: intraday residual *reversion* and
residual *momentum* are competing hypotheses, and which one survives costs is an
empirical question this framework exists to answer. Choosing between them by
looking at the equity curve is how research becomes overfitting — compare them
on the rank IC and on out-of-sample windows.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..data.sessions import at_or_after_market_time, session_date
from ..features.ranks import cross_sectional_zscore
from ..features.relative import (
    align_reference,
    bar_log_returns,
    residual_returns,
    rolling_beta,
    trailing_return,
)
from .base import MarketContext, Strategy, StrategySignals

MODES = ("reversion", "momentum")
REFERENCES = ("sector", "market")


class CrossSectionalResidualStrategy(Strategy):
    name = "cross_sectional_residual"

    def __init__(
        self,
        *,
        lookback: int = 30,
        beta_window: int = 120,
        reference: str = "sector",
        mode: str = "reversion",
        n_positions: int = 3,
        allow_short: bool = True,
        min_abs_zscore: float = 1.0,
        rebalance_bars: int = 15,
        min_eligible: int = 8,
        flat_time: str | None = "15:50",
    ):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if reference not in REFERENCES:
            raise ValueError(f"reference must be one of {REFERENCES}, got {reference!r}")
        if n_positions < 1:
            raise ValueError("n_positions must be at least 1")
        if rebalance_bars < 1:
            raise ValueError("rebalance_bars must be at least 1")

        self.lookback = int(lookback)
        self.beta_window = int(beta_window)
        self.reference = reference
        self.mode = mode
        self.n_positions = int(n_positions)
        self.allow_short = bool(allow_short)
        self.min_abs_zscore = float(min_abs_zscore)
        self.rebalance_bars = int(rebalance_bars)
        self.min_eligible = int(min_eligible)
        self.flat_time = flat_time

    # ---------------------------------------------------------------- signal
    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        returns = bar_log_returns(context.panel.close)

        reference = align_reference(returns, self._reference_map(context))
        beta = rolling_beta(returns[symbols], reference, self.beta_window)
        residual = residual_returns(returns[symbols], reference, beta)
        signal = trailing_return(residual, self.lookback)

        eligible = context.tradable[symbols] & signal.notna()
        # A cross-section too thin to rank is not a weak signal, it is no signal.
        has_breadth = eligible.sum(axis=1) >= self.min_eligible
        eligible = eligible.mul(has_breadth, axis=0).astype(bool)

        score = cross_sectional_zscore(signal, eligible)
        if self.mode == "reversion":
            score = -score

        weights = self._weights_from_score(score, eligible, context)
        indicators = {
            symbol: pd.DataFrame(
                {
                    "residual_return": residual[symbol],
                    "signal": signal[symbol],
                    "score": score[symbol],
                    "beta": beta[symbol],
                },
                index=context.index,
            )
            for symbol in symbols
        }
        return StrategySignals(target_weights=weights, scores=score, indicators=indicators)

    # ------------------------------------------------------------- internals
    def _reference_map(self, context: MarketContext) -> dict[str, str]:
        """Which series each symbol is measured against."""
        if self.reference == "market":
            return {symbol: context.universe.benchmark for symbol in context.symbols}
        sectors = context.universe.require_sectors()
        return {symbol: sectors[symbol] for symbol in context.symbols}

    def _weights_from_score(
        self, score: pd.DataFrame, eligible: pd.DataFrame, context: MarketContext
    ) -> pd.DataFrame:
        """Turn scores into a held book: select, size, hold, then flatten."""
        selectable = score.where(eligible)

        long_mask = (
            selectable.rank(axis=1, ascending=False, method="first").le(self.n_positions)
            & selectable.gt(self.min_abs_zscore)
        )
        if self.allow_short:
            short_mask = (
                selectable.rank(axis=1, ascending=True, method="first").le(self.n_positions)
                & selectable.lt(-self.min_abs_zscore)
            )
        else:
            short_mask = pd.DataFrame(False, index=score.index, columns=score.columns)

        # Each side gets its own half of the gross budget, so an empty short
        # side leaves capital unused instead of doubling the long exposure.
        side_budget = 0.5 if self.allow_short else 1.0
        weights = self._equal_weight(long_mask, side_budget) - self._equal_weight(
            short_mask, side_budget
        )

        weights = self._hold_between_rebalances(weights, context.index)
        weights = weights.where(eligible.reindex_like(weights), 0.0)

        if self.flat_time is not None:
            cutoff = dt.time.fromisoformat(self.flat_time)
            weights.loc[at_or_after_market_time(context.index, cutoff).to_numpy()] = 0.0
        return weights.fillna(0.0)

    @staticmethod
    def _equal_weight(mask: pd.DataFrame, budget: float) -> pd.DataFrame:
        """Split ``budget`` equally across the selected names in each row."""
        count = mask.sum(axis=1)
        return mask.astype(float).div(count.where(count > 0), axis=0).fillna(0.0) * budget

    def _hold_between_rebalances(
        self, weights: pd.DataFrame, index: pd.DatetimeIndex
    ) -> pd.DataFrame:
        """Keep the book fixed until the next rebalance bar of the same session."""
        day = session_date(index).to_numpy()
        bar_of_session = pd.Series(day, index=index).groupby(day).cumcount()
        is_rebalance = (bar_of_session % self.rebalance_bars == 0).to_numpy()

        held = weights.copy()
        held.loc[~is_rebalance] = np.nan
        return held.groupby(day).ffill().fillna(0.0)
