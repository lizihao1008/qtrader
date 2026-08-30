"""Ex-ante support and resistance levels.

Every level here is knowable at the bar it is attached to. That is the whole
point: the alternative — drawing lines on a finished chart — is hindsight
charting, and it is the single easiest way to manufacture a backtest that cannot
be traded.

Three families, following `docs/research/deep-research-report.md` §"支撑阻力必须
事前生成":

* **previous-day high / low** — fixed at the previous session's close;
* **opening-range high / low** — fixed once the opening window has elapsed, and
  unusable before then;
* **round numbers** — static, and the only family requiring no history at all.

A level is not a price but a zone: `[L - delta, L + delta]` with
`delta = max(1 tick, level_atr * ATR)`, so that touching it by a cent is not a
break. The ATR multiple is a stated starting value, not an optimised one.

Note for anyone reading results built on these: R07 measured the round-number
and previous-day families on this universe and found **no effect** — round-number
crossings continue at −0.0035 sigma against −0.0030 for placebo offsets, and
previous-day extremes are bracketed by placebos at 37%/63% of the prior range.
The levels are implemented faithfully; that does not make them informative.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date

#: Opening-range window, in bars. 30 minutes at 5-minute resolution.
OPENING_RANGE_BARS = 6


def average_true_range(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int,
    *, restart: pd.Series | None = None
) -> pd.DataFrame:
    """Wilder-style ATR on a wide frame, using the previous close.

    With ``restart`` (a session label per bar) the previous close is not carried
    across a session boundary, so the first bar of a session has a true range of
    ``high - low``. Without it, that bar's true range is dominated by the
    overnight gap — which would put yesterday's price into a width that decides
    what counts as a break today.
    """
    previous_close = close.shift(1)
    if restart is not None:
        first = pd.Series(restart).ne(pd.Series(restart).shift(1)).to_numpy()
        previous_close = previous_close.copy()
        previous_close.loc[first] = np.nan
    true_range = pd.concat(
        [
            (high - low).stack(),
            (high - previous_close).abs().stack(),
            (low - previous_close).abs().stack(),
        ],
        axis=1,
    ).max(axis=1).unstack()
    return true_range.reindex_like(close).rolling(window, min_periods=window // 2).mean()


def previous_day_levels(
    high: pd.DataFrame, low: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Previous session's high and low, broadcast across today's bars."""
    day = session_date(high.index).to_numpy()
    highs = high.groupby(day).max().shift(1)
    lows = low.groupby(day).min().shift(1)
    return (
        highs.reindex(day).set_axis(high.index),
        lows.reindex(day).set_axis(low.index),
    )


def opening_range_levels(
    high: pd.DataFrame, low: pd.DataFrame, bars: int = OPENING_RANGE_BARS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Today's opening-range high and low, NaN until the window has elapsed.

    The level is the extreme of the first ``bars`` bars and only exists from bar
    ``bars`` onward — using it earlier would be reading the range before it
    finished forming.
    """
    day = session_date(high.index).to_numpy()
    position = pd.Series(day, index=high.index).groupby(day).cumcount().to_numpy()
    opening = pd.DataFrame(
        np.repeat((position < bars)[:, None], high.shape[1], axis=1),
        index=high.index, columns=high.columns,
    )

    top = high.where(opening).groupby(day).max().reindex(day).set_axis(high.index)
    bottom = low.where(opening).groupby(day).min().reindex(day).set_axis(low.index)

    formed = pd.DataFrame(
        np.repeat((position >= bars)[:, None], high.shape[1], axis=1),
        index=high.index, columns=high.columns,
    )
    return top.where(formed), bottom.where(formed)


def nearest_round_levels(
    close: pd.DataFrame, step: float = 1.0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The round numbers immediately above and below each price."""
    below = np.floor(close / step) * step
    return below + step, below


def level_zone(atr: pd.DataFrame, close: pd.DataFrame, level_atr: float, tick: float = 0.01):
    """Half-width of the tolerance band around a level."""
    return np.maximum(atr * level_atr, tick)
