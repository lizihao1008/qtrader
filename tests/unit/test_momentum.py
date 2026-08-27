"""Session-restarted MACD and the calibrated crossing-speed statistic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.momentum import (
    macd_cross_zscore,
    session_macd,
    session_reset_norms,
)


def walks(trials: int, bars: int, sigma: float, seed: int, drift: float = 0.0):
    rng = np.random.default_rng(seed)
    returns = np.zeros((bars, trials))
    returns[1:] = rng.normal(drift, sigma, (bars - 1, trials))
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(returns, axis=0)))
    session = pd.Series(np.zeros(bars), index=close.index)
    bar_of_session = pd.Series(np.arange(bars), index=close.index)
    volatility = pd.DataFrame(sigma, index=close.index, columns=close.columns)
    return close, session, bar_of_session, volatility


# ------------------------------------------------------------------- the MACD
def test_the_macd_restarts_at_each_session():
    """An overnight gap must not reach into today's oscillator."""
    close = pd.DataFrame({"AAA": [100.0] * 50 + [200.0] * 50})
    session = pd.Series([0] * 50 + [1] * 50, index=close.index)

    panel = session_macd(close, session=session, fast=3, slow=8, signal=3)
    # Day two opens at a level twice day one's; a filter that carried across
    # would show an enormous spike. A restarted one starts from zero.
    assert panel["macd"]["AAA"].iloc[50] == pytest.approx(0.0)
    assert panel["macd_hist"]["AAA"].iloc[50] == pytest.approx(0.0)


def test_the_histogram_is_the_gap_between_line_and_signal():
    close, session, _, _ = walks(1, 200, 0.004, seed=1)
    panel = session_macd(close, session=session)
    pd.testing.assert_frame_equal(
        panel["macd_hist"], panel["macd"] - panel["macd_signal"]
    )


def test_a_fast_period_at_or_above_the_slow_one_is_rejected():
    close, session, _, _ = walks(1, 20, 0.004, seed=1)
    with pytest.raises(ValueError, match="must be shorter"):
        session_macd(close, session=session, fast=26, slow=12)


# -------------------------------------------------------------------- the norm
def test_the_norm_grows_from_zero_and_converges():
    norms = np.asarray(session_reset_norms(12, 26, 9))
    assert norms[0] == 0.0  # nothing has moved yet
    assert np.all(np.diff(norms[:20]) >= -1e-12)  # monotone while filling
    assert norms[-1] == pytest.approx(norms[100], rel=1e-6)  # converged


def test_the_norm_matches_a_simulated_filter():
    """The closed-form scale must equal the spread the filter actually produces."""
    sigma = 0.004
    close, session, bar_of_session, _ = walks(400, 300, sigma, seed=2)
    histogram = session_macd(close, session=session)["macd_hist"]

    change = (histogram.diff() / close).to_numpy()[150:]
    expected = sigma * session_reset_norms(12, 26, 9)[-1]
    assert change[np.isfinite(change)].std() == pytest.approx(expected, rel=0.05)


# --------------------------------------------------------------- the z-score
def test_the_crossing_zscore_is_standard_normal_at_every_bar():
    close, session, bar_of_session, volatility = walks(3_000, 200, 0.004, seed=3)
    histogram = session_macd(close, session=session)["macd_hist"]
    z = macd_cross_zscore(
        histogram, close, volatility=volatility, bar_of_session=bar_of_session
    ).to_numpy()

    assert np.isnan(z[0]).all()  # no previous histogram to difference against
    for bar in (1, 2, 10, 40, 120, 199):
        column = z[bar][np.isfinite(z[bar])]
        assert column.std() == pytest.approx(1.0, abs=0.06), bar
        assert (np.abs(column) >= 1.96).mean() == pytest.approx(0.05, abs=0.015), bar


def test_a_sharp_turn_scores_higher_than_a_drifting_one():
    """The statistic must rank a decisive crossing above an ambiguous one."""
    bars = 120
    session = pd.Series(np.zeros(bars))
    bar_of_session = pd.Series(np.arange(bars))
    sigma = 0.004

    gentle = np.r_[0.0, np.full(bars - 1, sigma * 0.1)]
    sharp = np.r_[np.zeros(60), np.full(bars - 60, sigma * 2.0)]
    close = pd.DataFrame(
        {"gentle": 100 * np.exp(np.cumsum(gentle)), "sharp": 100 * np.exp(np.cumsum(sharp))}
    )
    volatility = pd.DataFrame(sigma, index=close.index, columns=close.columns)

    histogram = session_macd(close, session=session)["macd_hist"]
    z = macd_cross_zscore(
        histogram, close, volatility=volatility, bar_of_session=bar_of_session
    )
    assert z["sharp"].abs().max() > 3 * z["gentle"].abs().max()


def test_the_zscore_is_scale_free_across_price_levels():
    """A $20 stock and a $600 one with identical relative moves must score alike."""
    bars = 150
    session = pd.Series(np.zeros(bars))
    bar_of_session = pd.Series(np.arange(bars))
    rng = np.random.default_rng(5)
    returns = np.r_[0.0, rng.normal(0.0005, 0.004, bars - 1)]

    close = pd.DataFrame(
        {"cheap": 20 * np.exp(np.cumsum(returns)), "dear": 600 * np.exp(np.cumsum(returns))}
    )
    volatility = pd.DataFrame(0.004, index=close.index, columns=close.columns)
    histogram = session_macd(close, session=session)["macd_hist"]
    z = macd_cross_zscore(
        histogram, close, volatility=volatility, bar_of_session=bar_of_session
    )
    pd.testing.assert_series_equal(z["cheap"], z["dear"], check_names=False, rtol=1e-9)
