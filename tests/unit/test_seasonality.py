"""Time-of-day volatility profile — it must be real, and it must be causal."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.seasonality import (
    MIN_SESSIONS,
    intraday_volatility_profile,
    seasonal_volatility,
)

BARS = 40
SESSIONS = 60


def panel(shape: np.ndarray, seed: int = 0, symbols: int = 4):
    """Returns whose volatility follows ``shape`` across each session."""
    rng = np.random.default_rng(seed)
    rows, days, minutes = [], [], []
    for session in range(SESSIONS):
        for minute in range(BARS):
            rows.append(rng.normal(0, 0.004 * shape[minute], symbols))
            days.append(session)
            minutes.append(minute)
    index = pd.RangeIndex(len(rows))
    return (
        pd.DataFrame(rows, index=index, columns=[f"S{i}" for i in range(symbols)]),
        pd.Series(days, index=index),
        pd.Series(minutes, index=index),
    )


def test_the_profile_recovers_an_injected_shape():
    """It encodes the *relative* shape: a day's own average is the unit."""
    shape = np.r_[np.full(5, 3.0), np.full(30, 1.0), np.full(5, 2.0)]
    returns, session, minute = panel(shape, seed=1)

    profile = intraday_volatility_profile(
        returns, session=session, bar_of_session=minute
    )
    settled = profile.iloc[-BARS:].to_numpy()  # the last session's fitted profile

    assert settled[2] / settled[20] == pytest.approx(3.0, rel=0.20)
    assert settled[37] / settled[20] == pytest.approx(2.0, rel=0.20)
    assert settled[2] > settled[37] > settled[20]


def test_the_profile_averages_to_one():
    """It redistributes volatility across the day; it must not change its level."""
    shape = np.r_[np.full(10, 4.0), np.full(30, 0.8)]
    returns, session, minute = panel(shape, seed=2)

    profile = intraday_volatility_profile(returns, session=session, bar_of_session=minute)
    settled = profile.iloc[-BARS:]
    assert (settled**2).mean() == pytest.approx(1.0, rel=0.05)


def test_the_profile_uses_only_completed_earlier_sessions():
    """Fitting on the whole sample would tell a strategy how wild today's open is."""
    shape = np.full(BARS, 1.0)
    returns, session, minute = panel(shape, seed=3)

    tampered = returns.copy()
    tail = session.to_numpy() >= SESSIONS - 10
    tampered.loc[tail] *= 25.0  # make the last ten sessions violent

    calm = intraday_volatility_profile(returns, session=session, bar_of_session=minute)
    wild = intraday_volatility_profile(tampered, session=session, bar_of_session=minute)

    cut = int(np.argmax(tail))
    pd.testing.assert_series_equal(calm.iloc[:cut], wild.iloc[:cut])


def test_the_profile_is_flat_until_enough_sessions_have_accumulated():
    shape = np.r_[np.full(5, 5.0), np.full(35, 1.0)]
    returns, session, minute = panel(shape, seed=4)

    profile = intraday_volatility_profile(returns, session=session, bar_of_session=minute)
    warmup = profile.iloc[: MIN_SESSIONS * BARS]
    assert (warmup == 1.0).all()


def test_seasonal_volatility_carries_the_shape_onto_the_level():
    shape = np.r_[np.full(5, 3.0), np.full(35, 1.0)]
    returns, session, minute = panel(shape, seed=5)

    flat = returns.rolling(BARS, min_periods=BARS // 2).std()
    shaped = seasonal_volatility(
        returns, session=session, bar_of_session=minute, window=BARS
    )

    opening = minute.to_numpy() < 5
    settled = session.to_numpy() > MIN_SESSIONS + 5
    ratio = (shaped[opening & settled] / flat[opening & settled]).stack().median()
    assert ratio > 2.0  # the open is judged against a much wider sigma


def test_an_extreme_session_cannot_distort_the_profile_without_bound():
    shape = np.full(BARS, 1.0)
    returns, session, minute = panel(shape, seed=6)
    returns.loc[session.to_numpy() == 5] *= 500.0

    profile = intraday_volatility_profile(returns, session=session, bar_of_session=minute)
    assert profile.max() <= 6.0
