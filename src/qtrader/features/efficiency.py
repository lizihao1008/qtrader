"""Kaufman's efficiency ratio: how much of a path's travel was progress.

    ER_n = |close_t - close_{t-n}| / sum(|close diffs| over the last n bars)

One for a straight line, near zero for chop that ends where it started. It is
the natural companion to a return: ``ret_15m`` says how far price went,
``ER_15m`` says whether it went there in a line or wandered. Two symbols with
the same 15-minute return are not the same setup if one trended and the other
whipsawed into position.

Windows never cross a session boundary. A ratio computed across the overnight
gap would put the gap in the numerator, where it would read as near-perfect
efficiency on the first bars of every day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date


def efficiency_ratio(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Kaufman efficiency ratio over ``window`` bars, per symbol.

    ``NaN`` until a full in-session window is available, and where the path did
    not move at all — a flat stretch has no direction to be efficient about, and
    reporting 0 there would be indistinguishable from a measured round trip.
    """
    if window < 2:
        raise ValueError("efficiency_ratio needs a window of at least 2 bars")

    day = pd.Series(session_date(close.index).to_numpy(), index=close.index)
    grouped = close.groupby(day.to_numpy())

    # Progress and travel are both taken inside the session, so the first
    # `window` bars of a day are NaN rather than reaching into yesterday.
    progress = grouped.transform(lambda s: (s - s.shift(window)).abs())
    steps = grouped.transform(lambda s: s.diff().abs())
    travel = steps.groupby(day.to_numpy()).transform(
        lambda s: s.rolling(window, min_periods=window).sum()
    )
    return progress / travel.where(travel > 0)


def signed_efficiency_ratio(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """``efficiency_ratio`` carrying the direction of the move.

    The unsigned ratio is a quality measure, not a direction: a clean sell-off
    scores as highly as a clean rally. A cross-sectional score that wants "clean
    move up" ranks better on this.
    """
    day = pd.Series(session_date(close.index).to_numpy(), index=close.index)
    direction = np.sign(
        close.groupby(day.to_numpy()).transform(lambda s: s - s.shift(window))
    )
    return efficiency_ratio(close, window) * direction
