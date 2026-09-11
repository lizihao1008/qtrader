"""Using a finer timeframe to confirm a decision taken on a coarser one.

A 5-minute strategy sees a move only after five minutes of it have elapsed. Many
intraday moves are largely over by then, so the coarse bar is simultaneously too
slow to enter early and too slow to notice the move ending. One-minute bars see
the same move sooner — at the cost of seeing far more noise that is not a move
at all. Combining them is a trade-off, not a free improvement.

Causality
---------
This is the only place the two grids meet, so the alignment rule lives here and
is asserted in one test.

A coarse bar labelled ``T`` spans ``[T, T + step)`` and **closes at T + step**.
The strategy decides at that close. The fine bars in existence at that moment are
exactly those labelled ``T .. T + step - 1``. So each coarse bar takes the *last*
fine observation inside its own span — no later one, and nothing is shifted
further back, because the strategy is already using the coarse bar's own close.

Getting this wrong in the obvious direction — taking the fine bar labelled
``T + step`` — would hand the strategy the first minute of the bar it is about to
be filled on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def align_to_coarse(fine: pd.DataFrame, coarse_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Last fine observation inside each coarse bar, on the coarse index.

    Coarse bars with no fine data are NaN, which every caller must treat as
    "no opinion" rather than as a zero.
    """
    if len(coarse_index) < 2:
        raise ValueError("need at least two coarse bars to infer the step")
    step = coarse_index[1] - coarse_index[0]

    index = pd.DatetimeIndex(fine.index)
    slot = np.searchsorted(coarse_index, index, side="right") - 1
    # Drop fine bars before the first coarse bar and after the last one's close.
    inside = (slot >= 0) & (index < coarse_index[-1] + step)
    if not inside.any():
        return pd.DataFrame(np.nan, index=coarse_index, columns=fine.columns)

    bucket = coarse_index[slot[inside]]
    last = fine.loc[inside].groupby(bucket).last()
    return last.reindex(coarse_index)


def align_to_fine(coarse: pd.DataFrame, fine_index: pd.DatetimeIndex) -> pd.DataFrame:
    """A coarse series on a fine grid, available only once each coarse bar closed.

    The mirror of `align_to_coarse`, and the more dangerous direction. A
    coarse bar labelled ``T`` spans ``[T, T + step)`` and is **not complete until
    T + step**. Broadcasting its value back across minutes ``T .. T+step-1`` —
    the obvious implementation — hands the fine grid a bar that has not finished
    forming, and on the first minute of it that is five minutes of hindsight.

    So each coarse value becomes visible at ``T + step`` and stays visible until
    the next one lands. Fine bars before the first coarse bar has closed are
    NaN, which every caller must read as "no opinion".
    """
    if len(coarse.index) < 2:
        raise ValueError("need at least two coarse bars to infer the step")
    step = coarse.index[1] - coarse.index[0]

    # Stamp each coarse observation with the moment it became knowable.
    known = coarse.copy()
    known.index = pd.DatetimeIndex(coarse.index) + step
    return known.reindex(pd.DatetimeIndex(fine_index), method="ffill")


def fine_drift_zscore(
    panel,
    symbols: list[str],
    coarse_index: pd.DatetimeIndex,
    *,
    span: int,
    volatility_window: int,
) -> pd.DataFrame:
    """Recency-weighted drift on the fine grid, reported on the coarse one.

    Standardised by the fine bars' own trailing volatility, so the number means
    the same thing as the coarse momentum statistic: drift in units of the
    sampling noise of the series it was measured on.
    """
    from ..data.sessions import session_date
    from .relative import bar_log_returns
    from .trend import ewma_drift_zscore

    close = panel.close[symbols]
    returns = bar_log_returns(close, within_session=True)
    volatility = returns.rolling(
        volatility_window, min_periods=max(volatility_window // 3, 5)
    ).std()
    day = pd.Series(session_date(close.index).to_numpy(), index=close.index)

    z = ewma_drift_zscore(returns, span, volatility=volatility, restart=day)
    return align_to_coarse(z, coarse_index)
