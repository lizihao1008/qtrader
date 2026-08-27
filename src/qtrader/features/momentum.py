"""MACD, restarted each session, and a calibrated measure of how hard it crosses.

A MACD crossover is usually read by eye: a steep crossing "means" a decisive
turn, a shallow one "means" indecision. That reading is real but the naive way
to measure it is not — the angle between two lines on a chart depends on the
price level and on how the chart was drawn, so the same relative move looks
steep on a $20 stock and flat on a $600 one.

What the eye is actually judging is **how fast the histogram is moving through
zero**. With ``h = MACD - signal``, the crossing is ``h`` changing sign and the
"angle" is ``dh/dt``. Making that comparable needs a scale, and the honest scale
is the same one used everywhere else here: what a driftless random walk would
have produced.

``h`` is a linear filter of the price level, so ``dh`` is a linear filter of
returns. Feeding a unit return shock (a step in the level) through the filter
gives its impulse response ``g``, and under the null::

    Var(dh_t) = (sigma_bar * P)^2 * sum_k g_k^2

so that

    cross_z = dh_t / (sigma_bar * P * ||g||)

is standard normal. ``|cross_z| >= 2`` then means the histogram is opening
faster than 95% of what noise alone would do — a genuinely sharp crossing —
while a value near zero is the shallow, ambiguous kind worth standing aside for.

Session restarts
----------------
The EMAs are restarted at each session open, so the statistic sees only today's
action and no overnight gap can leak into it (a gap would otherwise show up as a
spurious spike, because the fast EMA absorbs it sooner than the slow one).

A restarted filter is not the converged one, so its noise scale depends on how
far into the session it is: ``||g||`` is smaller in the first minutes because
fewer shocks have had time to contribute. The norms are therefore computed per
within-session position, once per parameter set, and cached.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

#: Within-session positions the norm table covers; a regular session is 390 bars.
MAX_SESSION_BARS = 400


def session_macd(
    close: pd.DataFrame,
    *,
    session: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, pd.DataFrame]:
    """MACD line, signal and histogram, with the EMAs restarted each session."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")

    groups = session.to_numpy()

    def ewm(frame: pd.DataFrame, span: int) -> pd.DataFrame:
        return frame.groupby(groups).transform(
            lambda column: column.ewm(span=span, adjust=False).mean()
        )

    line = ewm(close, fast) - ewm(close, slow)
    signal_line = ewm(line, signal)
    return {"macd": line, "macd_signal": signal_line, "macd_hist": line - signal_line}


@lru_cache(maxsize=16)
def session_reset_norms(
    fast: int, slow: int, signal: int, length: int = MAX_SESSION_BARS
) -> tuple[float, ...]:
    """``||g||`` for each within-session bar position.

    Row ``j`` of the probe is a unit step starting at bar ``j`` — the price path
    produced by a single return shock there — so column ``t`` of the differenced
    histogram holds the response of ``dh_t`` to every shock that could have
    reached it. The norm of that column is the scale ``dh_t`` should be judged
    against.
    """
    steps = np.triu(np.ones((length, length)))
    histogram = _histogram(steps, fast=fast, slow=slow, signal=signal)
    response = np.diff(histogram, axis=1, prepend=0.0)
    return tuple(np.sqrt((response**2).sum(axis=0)))


def macd_cross_zscore(
    histogram: pd.DataFrame,
    close: pd.DataFrame,
    *,
    volatility: pd.DataFrame,
    bar_of_session: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """How sharply the histogram is moving, in random-walk standard deviations.

    Signed: positive means the histogram is rising (a golden cross is opening
    upward), negative that it is falling. Take ``abs`` for "how decisive",
    compare the sign with the cross direction for "is it accelerating into it".
    """
    norms = np.asarray(session_reset_norms(fast, slow, signal))
    position = bar_of_session.to_numpy().clip(0, len(norms) - 1)
    scale_by_bar = pd.Series(norms[position], index=histogram.index)

    # A session's first bar has no previous histogram to difference against.
    change = histogram.diff()
    change.loc[bar_of_session.to_numpy() == 0] = np.nan

    scale = volatility * close
    scale = scale.mul(scale_by_bar, axis=0)
    return change / scale.where(scale > 0)


def _histogram(price: np.ndarray, *, fast: int, slow: int, signal: int) -> np.ndarray:
    """MACD histogram of many price paths at once, each restarted at column 0."""
    line = _ewm(price, fast) - _ewm(price, slow)
    return line - _ewm(line, signal)


def _ewm(values: np.ndarray, span: int) -> np.ndarray:
    """``adjust=False`` exponential mean along the last axis, seeded at column 0."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values)
    out[..., 0] = values[..., 0]
    for column in range(1, values.shape[-1]):
        out[..., column] = out[..., column - 1] + alpha * (
            values[..., column] - out[..., column - 1]
        )
    return out
