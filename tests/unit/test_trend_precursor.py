"""Trend precursor: causal momentum, no leakage, TOD-matched controls."""

from __future__ import annotations

import numpy as np
import pandas as pd

from qtrader.experiments.trend_precursor import (
    PrecursorConfig,
    build_precursor_samples,
    event_time_curves,
    precursor_statistics,
    _roc_auc,
)
from qtrader.features.lookback import log_momentum
from tests.conftest import make_bars, minute_index


def test_momentum_does_not_use_future_closes():
    prices = [100.0 + 0.1 * i for i in range(80)]
    original = log_momentum(make_bars(prices)["close"], 5)
    tampered = prices.copy()
    tampered[60:] = [p * 1.5 for p in tampered[60:]]
    after = log_momentum(make_bars(tampered)["close"], 5)
    pd.testing.assert_series_equal(original.iloc[:60], after.iloc[:60], check_names=False)


def test_momentum_does_not_cross_the_overnight_gap():
    first = minute_index(60, start_local="2026-08-03 09:30")
    second = minute_index(60, start_local="2026-08-04 09:30")
    prices = [100.0] * 60 + [130.0] * 60
    close = make_bars(prices, index=first.append(second))["close"]
    mom = log_momentum(close, 5)
    assert not np.isfinite(mom.loc[second[:5]]).any()
    assert np.isfinite(mom.loc[second[5]])


def test_momentum_skips_a_missing_minute():
    index = minute_index(40)
    close = make_bars([100.0 + i for i in range(40)], index=index)["close"]
    hole = close.drop(close.index[20])
    mom = log_momentum(hole, 5)
    assert not np.isfinite(mom.iloc[20])
    assert not np.isfinite(mom.iloc[21])


def _two_day_climb():
    day1 = minute_index(90, start_local="2026-08-03 11:00")
    day2 = minute_index(90, start_local="2026-08-04 11:00")
    p1 = [100.0 + 0.02 * i for i in range(90)]
    p2 = [100.0 + 0.02 * i for i in range(90)]
    for i in range(50, 70):
        p1[i] = p1[i - 1] + 0.25
    bars = make_bars(p1 + p2, index=day1.append(day2))
    event_open = day1[60]
    events = pd.DataFrame(
        {
            "symbol": ["QQQ"],
            "bar_open": [event_open],
            "trend_start": [event_open + pd.Timedelta(minutes=1)],
            "direction": [1],
            "ztrend": [2.0],
            "ER": [0.5],
        }
    )
    return bars, events


def test_factor_ends_before_the_event_bar():
    bars, events = _two_day_climb()
    cfg = PrecursorConfig(n_bootstrap=0, controls_per_event=3, random_seed=42)
    samples = build_precursor_samples(events, {"QQQ": bars}, cfg)
    trend = samples.loc[samples["sample_type"] == "trend"].iloc[0]
    assert trend["factor_end_time"] < trend["bar_open"]
    assert trend["factor_end_time"] < trend["trend_start"]
    tampered = bars.copy()
    tampered.loc[trend["bar_open"], "close"] = float(bars["close"].loc[trend["bar_open"]]) * 3
    again = build_precursor_samples(events, {"QQQ": tampered}, cfg)
    t2 = again.loc[again["sample_type"] == "trend"].iloc[0]
    for col in [c for c in samples.columns if c.startswith("mom_")]:
        if np.isfinite(trend[col]):
            assert t2[col] == trend[col]


def test_controls_are_outside_the_exclusion_window_and_near_the_tod():
    bars, events = _two_day_climb()
    cfg = PrecursorConfig(
        n_bootstrap=0, controls_per_event=5,
        exclusion_minutes=30, tod_tolerance_minutes=30, random_seed=42,
    )
    samples = build_precursor_samples(events, {"QQQ": bars}, cfg)
    event_open = events.iloc[0]["bar_open"]
    controls = samples.loc[samples["sample_type"] == "control"]
    assert not controls.empty
    for stamp in controls["bar_open"]:
        delta = abs(pd.Timestamp(stamp) - event_open)
        assert delta >= pd.Timedelta(minutes=30)
        tod_event = event_open.tz_convert("America/New_York")
        tod_ctrl = pd.Timestamp(stamp).tz_convert("America/New_York")
        minutes = abs(
            (tod_ctrl.hour * 60 + tod_ctrl.minute) - (tod_event.hour * 60 + tod_event.minute)
        )
        assert minutes <= 30


def test_the_same_seed_draws_the_same_controls():
    bars, events = _two_day_climb()
    cfg = PrecursorConfig(n_bootstrap=0, controls_per_event=4, random_seed=42)
    a = build_precursor_samples(events, {"QQQ": bars}, cfg)
    b = build_precursor_samples(events, {"QQQ": bars}, cfg)
    pd.testing.assert_frame_equal(a, b)


def test_roc_auc_is_one_for_a_perfect_separator():
    y = np.array([0, 0, 0, 1, 1, 1], dtype=float)
    s = np.array([0.0, 0.1, 0.2, 0.8, 0.9, 1.0])
    assert abs(_roc_auc(y, s) - 1.0) < 1e-12
    assert abs(_roc_auc(y, -s) - 0.0) < 1e-12


def test_statistics_table_has_every_window_and_lead():
    bars, events = _two_day_climb()
    cfg = PrecursorConfig(n_bootstrap=0, controls_per_event=3, random_seed=42)
    samples = build_precursor_samples(events, {"QQQ": bars}, cfg)
    stats = precursor_statistics(samples, cfg)
    assert set(stats["window"]) == {1, 3, 5, 10, 20}
    assert set(stats["lead"]) == {1, 3, 5, 10}
    assert set(stats["side"]) == {"up", "down", "directional"}


def test_event_time_curve_stops_before_the_event():
    bars, events = _two_day_climb()
    cfg = PrecursorConfig(n_bootstrap=0, controls_per_event=3, random_seed=42)
    samples = build_precursor_samples(events, {"QQQ": bars}, cfg)
    curves = event_time_curves(samples, {"QQQ": bars}, cfg)
    assert curves["lag"].max() == -1
    assert curves["lag"].min() == -30
