"""Time-of-day volatility, and why a flat sigma is not good enough.

Intraday volatility is not constant. Pooled over 22 names and 142 sessions of
1-minute bars, the standard deviation of returns by minute of session runs::

    minute      1     5    15    30    60   120   240   380
    x mean   4.11  2.62  2.12  1.75  1.22  0.92  0.75  1.64

the familiar U: violent at the open, quiet through midday, lively into the
close. A volatility estimate that averages over the whole day therefore
**understates the open by three to four times**.

Everything in this project that divides by sigma inherits that error. A trend
z-score built on a flat sigma is inflated by the same factor, so a nominal
2.5-sigma threshold is really asking for 0.85 sigma at minute 3 — no filter at
all. That is not a hypothetical: it put twelve of thirteen losing trades in one
test week inside the first fifteen minutes of a session.

The fix is a multiplicative seasonal factor, ``sigma(symbol, t) = sigma(symbol)
* profile(minute of session)``, with the profile normalised to average 1 so it
redistributes volatility across the day without changing its level.

Causality
---------
The profile must be estimated from **prior sessions only**. Fitting it on the
whole sample would let a strategy know today how volatile today's open was,
which is precisely the kind of leak this codebase exists to avoid. It is built
here with an expanding mean over completed sessions, shifted by one, and falls
back to a flat profile until ``min_sessions`` have accumulated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Sessions of history required before a fitted profile is used at all.
MIN_SESSIONS = 10

#: The profile is clipped to this band. A single wild session should not be
#: able to claim that some minute is twenty times more volatile than the rest.
PROFILE_LIMITS = (0.25, 6.0)


def intraday_volatility_profile(
    returns: pd.DataFrame,
    *,
    session: pd.Series,
    bar_of_session: pd.Series,
    min_sessions: int = MIN_SESSIONS,
) -> pd.Series:
    """Volatility multiplier per bar, fitted on completed prior sessions only.

    Returns a series aligned to ``returns.index`` whose value at each bar is the
    expected volatility at that minute of the session relative to an average
    minute. Multiply a flat sigma estimate by it to get a seasonally aware one.
    """
    days = session.to_numpy()
    minutes = bar_of_session.to_numpy()

    # Cross-sectional dispersion at each (session, minute): one number per cell.
    scale = pd.DataFrame(
        {"day": days, "minute": minutes, "scale": (returns**2).mean(axis=1).to_numpy()}
    )
    grid = scale.pivot_table(index="day", columns="minute", values="scale", aggfunc="mean")

    # Expanding mean over *earlier* sessions only, then normalised to average 1.
    history = grid.expanding().mean().shift(1)
    normalised = history.div(history.mean(axis=1), axis=0)
    normalised = np.sqrt(normalised).clip(*PROFILE_LIMITS)
    normalised.iloc[:min_sessions] = np.nan

    lookup = normalised.stack().rename("profile")
    keyed = pd.MultiIndex.from_arrays([days, minutes], names=["day", "minute"])
    return pd.Series(lookup.reindex(keyed).to_numpy(), index=returns.index).fillna(1.0)


def seasonal_volatility(
    returns: pd.DataFrame,
    *,
    session: pd.Series,
    bar_of_session: pd.Series,
    window: int,
    min_periods: int | None = None,
    min_sessions: int = MIN_SESSIONS,
) -> pd.DataFrame:
    """Per-bar volatility with the time-of-day shape restored.

    The level comes from a rolling estimate over ``window`` bars — which averages
    across the day and so measures the symbol's overall activity — and the shape
    comes from the seasonal profile. Their product is what a bar at this minute
    of this symbol should be judged against.
    """
    profile = intraday_volatility_profile(
        returns, session=session, bar_of_session=bar_of_session, min_sessions=min_sessions
    )
    level = returns.rolling(window, min_periods=min_periods or window // 2).std()
    return level.mul(profile, axis=0)


def seasonal_volume_ratio(
    volume: pd.DataFrame,
    *,
    session: pd.Series,
    bar_of_session: pd.Series,
    window: int = 5,
    min_sessions: int = MIN_SESSIONS,
) -> pd.DataFrame:
    """Relative volume against the same minute of earlier sessions (RVOL).

    Intraday volume has a pronounced U-shape: the first minutes of a session
    carry many times the volume of the middle of the day. Comparing a bar to a
    flat rolling average therefore reports "unusually busy" for every open and
    "unusually quiet" for every lunchtime, which is a clock, not a signal.

    The denominator is the mean volume at *this minute of the session*, taken
    over completed prior sessions only — expanded and then shifted by one
    session, so today never contributes to its own baseline. The numerator is
    the trailing ``window``-bar mean, so a single print does not dominate.

    ``NaN`` until ``min_sessions`` of history exist, rather than 1.0: pretending
    an unknown baseline is average would let the first two weeks of any run
    trade on a number that was never measured.
    """
    days = session.to_numpy()
    minutes = bar_of_session.to_numpy()
    # Reset at the open: a 5-bar mean at 09:31 must not average four of
    # yesterday's closing minutes, which are the busiest of the day.
    recent = volume.groupby(days).transform(
        lambda s: s.rolling(window, min_periods=1).mean()
    )

    ratios = {}
    for symbol in volume.columns:
        grid = pd.DataFrame(
            {"day": days, "minute": minutes, "v": volume[symbol].to_numpy()}
        ).pivot_table(index="day", columns="minute", values="v", aggfunc="mean")
        baseline = grid.expanding().mean().shift(1)
        baseline.iloc[:min_sessions] = np.nan
        lookup = baseline.stack(future_stack=True).rename("baseline")
        keyed = pd.MultiIndex.from_arrays([days, minutes], names=["day", "minute"])
        expected = pd.Series(lookup.reindex(keyed).to_numpy(), index=volume.index)
        ratios[symbol] = recent[symbol] / expected.where(expected > 0)
    return pd.DataFrame(ratios, index=volume.index, columns=volume.columns)
