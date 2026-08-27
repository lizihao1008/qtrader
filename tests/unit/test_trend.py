"""The trend statistic — and the null distribution it claims to have."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.trend import (
    drift_zscore,
    ewma_drift_zscore,
    random_walk_slope_scale,
    rolling_slope,
    slope_variance,
)
from tests.conftest import minute_index

WINDOW = 40


def frame(values) -> pd.DataFrame:
    return pd.DataFrame({"AAA": list(values)}, index=minute_index(len(values)))


# ------------------------------------------------------------------ estimator
def test_the_rolling_slope_matches_an_explicit_regression():
    rng = np.random.default_rng(4)
    path = frame(np.cumsum(rng.normal(0.0004, 0.004, 200)) + 4.6)
    slope = rolling_slope(path, WINDOW)["AAA"]

    for position in (WINDOW - 1, 90, 199):
        window = path["AAA"].iloc[position - WINDOW + 1 : position + 1].to_numpy()
        expected, _ = np.polyfit(np.arange(WINDOW), window, 1)
        assert slope.iloc[position] == pytest.approx(expected, rel=1e-9)


def test_leverage_grows_cubically_with_the_window():
    assert slope_variance(60) == pytest.approx(60 * (60**2 - 1) / 12)
    assert slope_variance(120) / slope_variance(60) == pytest.approx(8.0, rel=1e-3)


def test_the_slope_scale_matches_a_simulated_random_walk():
    """The closed form for sd(slope) must equal what a random walk produces."""
    rng = np.random.default_rng(1)
    sigma, trials = 0.01, 20_000
    walks = np.cumsum(rng.normal(0.0, sigma, (trials, WINDOW)), axis=1)

    index = np.arange(WINDOW)
    slopes = np.polyfit(index, walks.T, 1)[0]
    assert slopes.std() == pytest.approx(sigma * random_walk_slope_scale(WINDOW), rel=0.03)


# ----------------------------------------------------------------- the z-score
def test_the_zscore_is_standard_normal_on_driftless_random_walks():
    """The whole point: |z| >= 2 must be rare when there is no trend."""
    rng = np.random.default_rng(5)
    walks = pd.DataFrame(np.cumsum(rng.normal(0, 0.004, (4_000, 30)), axis=0) + 4.6)

    z = drift_zscore(walks, 60).to_numpy()
    z = z[np.isfinite(z)]

    assert z.mean() == pytest.approx(0.0, abs=0.05)
    assert z.std() == pytest.approx(1.0, abs=0.06)
    assert (np.abs(z) >= 1.96).mean() == pytest.approx(0.05, abs=0.015)


def test_a_real_drift_is_detected():
    rng = np.random.default_rng(6)
    drifted = pd.DataFrame(np.cumsum(rng.normal(0.0008, 0.004, (4_000, 30)), axis=0) + 4.6)

    z = drift_zscore(drifted, 60).to_numpy()
    z = z[np.isfinite(z)]
    assert z.mean() > 1.0
    assert (z >= 2.0).mean() > 0.15


def test_the_zscore_is_scale_free():
    """Doubling the price level leaves the trend's signal-to-noise unchanged."""
    rng = np.random.default_rng(7)
    log_path = np.cumsum(rng.normal(0.0005, 0.003, 200)) + 3.0

    plain = drift_zscore(frame(log_path), WINDOW)["AAA"]
    doubled = drift_zscore(frame(log_path + np.log(2)), WINDOW)["AAA"]
    pd.testing.assert_series_equal(plain, doubled)


def test_a_flat_line_has_no_trend_rather_than_an_infinite_one():
    assert pd.isna(drift_zscore(frame(np.full(80, 5.0)), WINDOW)["AAA"].iloc[-1])


def test_the_zscore_is_causal():
    rng = np.random.default_rng(2)
    path = list(np.cumsum(rng.normal(0, 0.003, 200)) + 4.6)
    tampered = path[:120] + [9.0] * 80

    pd.testing.assert_series_equal(
        drift_zscore(frame(path), WINDOW)["AAA"].iloc[:120],
        drift_zscore(frame(tampered), WINDOW)["AAA"].iloc[:120],
    )


def test_a_window_too_short_to_estimate_drift_is_rejected():
    with pytest.raises(ValueError, match="window must be at least"):
        rolling_slope(frame([1.0, 2.0, 3.0]), 3)


# --------------------------------------------------------- the EWMA estimator
def noise_panel(trials: int, bars: int, sigma: float, drift: float = 0.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    returns = pd.DataFrame(rng.normal(drift, sigma, (bars, trials)))
    returns.iloc[0] = 0.0  # the session's first bar carries no return
    volatility = pd.DataFrame(sigma, index=returns.index, columns=returns.columns)
    restart = pd.Series(np.zeros(bars), index=returns.index)
    return returns, volatility, restart


def test_the_ewma_zscore_is_standard_normal_at_every_bar():
    """The whole reason for it: calibrated from the start, not after a window."""
    returns, volatility, restart = noise_panel(3_000, 390, 0.004, seed=30)
    z = ewma_drift_zscore(returns, 60, volatility=volatility, restart=restart).to_numpy()

    assert np.isnan(z[0]).all()  # nothing to say on the session's first bar
    for bar in (1, 2, 10, 60, 200, 389):
        column = z[bar]
        assert column.std() == pytest.approx(1.0, abs=0.06), bar
        assert (np.abs(column) >= 1.96).mean() == pytest.approx(0.05, abs=0.015), bar


def test_the_ewma_zscore_demands_more_evidence_when_it_has_seen_less():
    """One bar of history and sixty must not treat the same move alike."""
    sigma = 0.004
    move = pd.DataFrame({"AAA": [0.0] + [sigma] * 200})
    volatility = pd.DataFrame(sigma, index=move.index, columns=move.columns)
    restart = pd.Series(np.zeros(len(move)), index=move.index)

    z = ewma_drift_zscore(move, 60, volatility=volatility, restart=restart)["AAA"]
    assert z.iloc[1] < z.iloc[10] < z.iloc[60]  # same per-bar drift, rising confidence


def test_the_ewma_zscore_detects_drift():
    returns, volatility, restart = noise_panel(2_000, 390, 0.004, drift=0.0008, seed=31)
    z = ewma_drift_zscore(returns, 60, volatility=volatility, restart=restart).to_numpy()
    assert np.nanmean(z[120]) > 1.0


def test_the_ewma_zscore_restarts_at_each_session():
    """Yesterday's drift must not colour today's opening reading."""
    sigma = 0.004
    returns = pd.DataFrame({"AAA": [0.0] + [sigma * 3] * 99 + [0.0] + [0.0] * 99})
    volatility = pd.DataFrame(sigma, index=returns.index, columns=returns.columns)
    restart = pd.Series([0] * 100 + [1] * 100, index=returns.index)

    z = ewma_drift_zscore(returns, 30, volatility=volatility, restart=restart)["AAA"]
    assert z.iloc[99] > 5.0  # a strong first session
    assert pd.isna(z.iloc[100])  # the second session starts with no opinion
    assert abs(z.iloc[110]) < 1.0  # and is not inheriting the first one's drift
