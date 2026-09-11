"""Causal lookback momentum: ``log(close_t / close_{t-N})`` on an exact clock.

The window is N *calendar bars of this timeframe*, same session, exact time
span. A missing minute or the overnight gap yields NaN — nothing is filled.
Available at the **close** of bar ``t`` (store timestamp is the open).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date


def log_momentum(
    close: pd.Series,
    n: int,
    *,
    bar_minutes: int = 1,
) -> pd.Series:
    """``log(close_t / close_{t-n})`` when ``t-n`` is exactly ``n`` bars earlier."""
    if n < 1:
        raise ValueError("n must be at least 1")
    close = close.astype(float)
    index = pd.DatetimeIndex(close.index)
    ok = exact_lookback(index, n, bar_minutes=bar_minutes)
    lagged = close.shift(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        mom = np.log(close / lagged)
    return mom.where(ok)


def exact_lookback(
    index: pd.DatetimeIndex,
    n: int,
    *,
    bar_minutes: int = 1,
) -> pd.Series:
    """True at ``t`` iff the bar ``t-n`` exists, same session, exact time span."""
    session = session_date(index).to_numpy()
    expected = n * pd.Timedelta(minutes=bar_minutes)
    target = np.arange(len(index)) - n
    valid = target >= 0
    out = np.zeros(len(index), dtype=bool)
    idx = np.flatnonzero(valid)
    src = target[idx]
    span = index[idx] - index[src]
    out[idx] = (session[idx] == session[src]) & (span == expected)
    return pd.Series(out, index=index)
