"""Trend-start labels: direction, chop rejection, no sigma leakage, dedup."""

from __future__ import annotations

import pandas as pd

from qtrader.labels.trend_events import (
    TrendDetectConfig,
    deduplicate_trend_events,
    detect_trend_events,
    score_trend_candidates,
)
from tests.conftest import make_bars, minute_index

CFG = TrendDetectConfig(
    horizon=20,
    vol_lookback=40,
    sigma_floor=1e-6,
    z_threshold=1.5,
    er_threshold=0.40,
    mae_threshold=0.50,
    cooldown_bars=20,
)


def _rally(n: int = 120) -> list[float]:
    quiet = [100.0] * 50
    climb = [100.0 + 0.25 * (i + 1) for i in range(n - 50)]
    return quiet + climb


def _selloff(n: int = 120) -> list[float]:
    quiet = [100.0] * 50
    fall = [100.0 - 0.25 * (i + 1) for i in range(n - 50)]
    return quiet + fall


def _chop(n: int = 120) -> list[float]:
    """Large two-way swings that still finish higher. Not a clean trend."""
    price = 100.0
    out = []
    for i in range(n):
        price += 0.80 if i % 2 == 0 else -0.70
        out.append(price)
    return out


def test_a_window_that_opens_the_wrong_way_is_not_a_candidate():
    """A down-label may not start with an up bar, even if the H-window still dumps."""
    price = 100.0
    prices = []
    for i in range(60):
        price += 0.08 if i % 2 == 0 else -0.07
        prices.append(price)
    bounce_from = len(prices)
    for _ in range(8):
        price += 0.12
        prices.append(price)
    for _ in range(52):
        price -= 0.35
        prices.append(price)
    bars = make_bars(prices)
    loose = TrendDetectConfig(
        horizon=20, vol_lookback=40, sigma_floor=1e-6,
        z_threshold=1.5, er_threshold=0.40, mae_threshold=0.50,
        cooldown_bars=20, require_first_bar_aligned=False,
    )
    aligned = TrendDetectConfig(
        horizon=20, vol_lookback=40, sigma_floor=1e-6,
        z_threshold=1.5, er_threshold=0.40, mae_threshold=0.50,
        cooldown_bars=20, require_first_bar_aligned=True,
    )
    scored_loose = score_trend_candidates(bars, "QQQ", loose)
    scored_aligned = score_trend_candidates(bars, "QQQ", aligned)
    wrong_open = scored_loose.loc[
        (scored_loose["candidate_direction"] == -1) & (scored_loose["first_return"] > 0)
    ]
    assert not wrong_open.empty
    assert (scored_aligned.loc[wrong_open.index, "candidate_direction"] == 0).all()
    peak = bars.index[bounce_from + 7]
    assert scored_aligned.loc[peak, "first_return"] < 0
    assert int(scored_aligned.loc[peak, "candidate_direction"]) == -1


def test_a_one_tick_lead_then_a_larger_dip_is_an_opening_reversal():
    """A +1 tick first close does not license an immediate dump through the start."""
    price = 100.0
    prices = []
    for i in range(60):
        price += 0.08 if i % 2 == 0 else -0.07
        prices.append(price)
    pop_from = len(prices)
    prices.append(price + 0.01)
    price = prices[-1]
    prices.append(price - 0.20)
    price = prices[-1]
    for _ in range(58):
        price += 0.30
        prices.append(price)
    bars = make_bars(prices)
    loose = TrendDetectConfig(
        horizon=20, vol_lookback=40, sigma_floor=1e-6,
        z_threshold=1.5, er_threshold=0.40, mae_threshold=0.50,
        cooldown_bars=20, require_first_bar_aligned=False,
    )
    aligned = TrendDetectConfig(
        horizon=20, vol_lookback=40, sigma_floor=1e-6,
        z_threshold=1.5, er_threshold=0.40, mae_threshold=0.50,
        cooldown_bars=20, require_first_bar_aligned=True,
    )
    at_pop = bars.index[pop_from - 1]
    scored_loose = score_trend_candidates(bars, "QQQ", loose)
    scored_aligned = score_trend_candidates(bars, "QQQ", aligned)
    only_loose = (scored_loose["candidate_direction"] == 1) & (
        scored_aligned["candidate_direction"] == 0
    )
    assert only_loose.any()
    rejected = scored_loose.loc[only_loose]
    assert (rejected["first_return"] > 0).all()
    assert (rejected["MAE_up"] >= rejected["first_return"]).all()
    after_dip = bars.index[pop_from + 1]
    assert int(scored_aligned.loc[after_dip, "candidate_direction"]) == 1


def test_every_candidate_opens_in_the_labelled_direction():
    for prices in (_rally(), _selloff()):
        scored = score_trend_candidates(make_bars(prices), "QQQ", CFG)
        labelled = scored.loc[scored["candidate_direction"] != 0]
        assert (labelled["first_return"] * labelled["candidate_direction"] >= 0).all()


def test_a_monotone_rally_is_an_up_event():
    bars = make_bars(_rally())
    candidates, events = detect_trend_events(bars, "QQQ", CFG)
    assert not events.empty
    assert set(events["direction"]) == {1}
    assert (events["ER"] >= CFG.er_threshold).all()
    assert (events["ztrend"] >= CFG.z_threshold).all()
    assert (candidates["candidate_direction"] == 1).all()


def test_a_monotone_selloff_is_a_down_event():
    bars = make_bars(_selloff())
    _, events = detect_trend_events(bars, "QQQ", CFG)
    assert not events.empty
    assert set(events["direction"]) == {-1}
    assert (events["ztrend"] <= -CFG.z_threshold).all()


def test_a_choppy_path_that_finishes_higher_is_not_a_clean_trend():
    bars = make_bars(_chop())
    scored = score_trend_candidates(bars, "QQQ", CFG)
    labelled = scored.loc[scored["candidate_direction"] != 0]
    assert labelled.empty, (
        f"chop produced {len(labelled)} candidates; "
        f"ER={scored['ER'].median():.3f} MAE_up_norm={scored['MAE_up_norm'].median():.3f}"
    )


def test_rewriting_the_future_does_not_change_sigma_t():
    n = 120
    prices = _rally(n)
    original = score_trend_candidates(make_bars(prices), "QQQ", CFG)["sigma"]
    cut = 80
    tampered = prices[:cut] + [p * 1.4 for p in prices[cut:]]
    after = score_trend_candidates(make_bars(tampered), "QQQ", CFG)["sigma"]
    pd.testing.assert_series_equal(
        original.iloc[:cut], after.iloc[:cut], check_names=False
    )


def test_a_run_of_candidates_collapses_to_one_trend_start():
    bars = make_bars(_rally())
    candidates, events = detect_trend_events(bars, "QQQ", CFG)
    assert len(events) < len(candidates)
    assert events["candidate_run_length"].min() >= 1
    starts = pd.DatetimeIndex(events["bar_open"]).sort_values()
    gaps = starts.to_series().diff().dt.total_seconds() / 60
    later = gaps.dropna()
    if not later.empty:
        assert (later >= CFG.cooldown).all()


def test_a_second_same_direction_run_inside_cooldown_is_not_a_new_event():
    """Cooldown is counted from the run's last bar, so one grind is not sliced every H minutes."""
    index = minute_index(40)
    direction = [0] * 40
    direction[0:8] = [1] * 8
    direction[25] = 1  # 17 bars after the run ended; cooldown is 20
    scored = pd.DataFrame(
        {
            "symbol": "QQQ",
            "bar_open": index,
            "trend_start": index + pd.Timedelta(minutes=1),
            "candidate_direction": direction,
            "horizon": 20,
            "future_return": 0.01,
            "first_return": 0.001,
            "ztrend": 2.0,
            "ER": 0.8,
            "sigma": 0.001,
            "MAE_up": 0.0, "MAE_down": 0.0,
            "MAE_up_norm": 0.1, "MAE_down_norm": 0.1,
            "MFE_up": 0.02, "MFE_down": 0.02,
            "MFE_up_norm": 1.0, "MFE_down_norm": 1.0,
            "start_close": 100.0, "end_close": 101.0,
        },
        index=index,
    )
    events = deduplicate_trend_events(scored, TrendDetectConfig(horizon=20, cooldown_bars=20))
    assert len(events) == 1
    assert events.iloc[0]["candidate_run_length"] == 8


def test_an_opposite_candidate_inside_cooldown_is_kept_not_deleted():
    index = minute_index(8)
    scored = pd.DataFrame(
        {
            "symbol": "QQQ",
            "bar_open": index,
            "trend_start": index + pd.Timedelta(minutes=1),
            "candidate_direction": [1, 1, 1, 0, -1, -1, 0, 0],
            "horizon": 20,
            "future_return": 0.01,
            "first_return": [0.001, 0.001, 0.001, 0.0, -0.001, -0.001, 0.0, 0.0],
            "ztrend": [2.0, 2.0, 2.0, 0.0, -2.0, -2.0, 0.0, 0.0],
            "ER": 0.8,
            "sigma": 0.001,
            "MAE_up": 0.0, "MAE_down": 0.0,
            "MAE_up_norm": 0.1, "MAE_down_norm": 0.1,
            "MFE_up": 0.02, "MFE_down": 0.02,
            "MFE_up_norm": 1.0, "MFE_down_norm": 1.0,
            "start_close": 100.0, "end_close": 101.0,
        },
        index=index,
    )
    events = deduplicate_trend_events(scored, TrendDetectConfig(horizon=20, cooldown_bars=20))
    assert set(events["direction"]) == {1, -1}
    down = events.loc[events["direction"] == -1].iloc[0]
    assert bool(down["overlaps_opposite"]) is True


def test_a_missing_minute_does_not_join_the_future_window():
    prices = _rally(100)
    index = minute_index(100)
    bars = make_bars(prices, index=index)
    hole = bars.drop(bars.index[60])
    scored = score_trend_candidates(hole, "QQQ", CFG)
    around = scored.iloc[40:61]
    assert not around["valid_future"].all()


def test_the_overnight_gap_is_not_a_one_minute_return():
    first = minute_index(60)
    second = minute_index(60, start_local="2026-08-04 09:30")
    prices = [100.0] * 60 + [130.0] * 60
    bars = make_bars(prices, index=first.append(second))
    scored = score_trend_candidates(bars, "QQQ", CFG)
    day2 = scored.loc[second]
    assert not bool(day2["valid_sigma"].iloc[0])
    assert not bool(day2["valid_sigma"].iloc[20])
    assert bool(day2["valid_sigma"].iloc[39])


def test_trend_start_is_the_bar_end_not_the_open():
    bars = make_bars(_rally())
    _, events = detect_trend_events(bars, "QQQ", CFG)
    delta = pd.DatetimeIndex(events["trend_start"]) - pd.DatetimeIndex(events["bar_open"])
    assert (delta == pd.Timedelta(minutes=1)).all()


def test_a_chart_marks_the_start_and_the_horizon():
    from qtrader.viz.trend_events import trend_event_chart

    bars = make_bars(_rally())
    _, events = detect_trend_events(bars, "QQQ", CFG)
    fig = trend_event_chart(bars, events.iloc[0])
    shapes = fig.layout.shapes or ()
    vlines = [s for s in shapes if s.x0 == s.x1]
    assert len(vlines) >= 2
    title = fig.layout.title.text
    assert "UP TREND" in title
    assert "Z=" in title
    assert fig.layout.showlegend is False
    assert fig.layout.margin.t >= 90


def test_a_late_session_chart_does_not_draw_the_next_day():
    """Integer post-window bars after the close must not become Monday on the axis."""
    from qtrader.viz.trend_events import trend_event_chart

    friday = minute_index(60, start_local="2026-09-04 15:00")
    monday = minute_index(60, start_local="2026-09-08 09:30")
    bars = make_bars(
        [100.0 + 0.1 * i for i in range(120)],
        index=friday.append(monday),
    )
    event = pd.Series(
        {
            "symbol": "QQQ",
            "bar_open": friday[20],
            "trend_start": friday[20] + pd.Timedelta(minutes=1),
            "direction": 1,
            "horizon": 30,
            "future_return": 0.01,
            "ztrend": 1.8,
            "ER": 0.42,
            "MAE_norm": 0.06,
            "MFE_norm": 1.8,
        }
    )
    fig = trend_event_chart(bars, event, pre_window=40, post_extra=10)
    dates = pd.DatetimeIndex(fig.data[0].x).normalize().unique()
    assert list(dates.date) == [pd.Timestamp("2026-09-04").date()]
    assert "2026-09-04" in fig.layout.title.text
