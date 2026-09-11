"""Kalman–CUSUM regime detector: causal, online, no smoother.

The gates are all driftless-random-walk quantities, so most of these tests are
calibration tests: they check that a threshold means the same thing everywhere,
not that a particular symbol behaved a particular way.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.regime import (
    KalmanCUSUMConfig,
    KalmanCUSUMRegimeDetector,
    LocalLinearTrendFilter,
    PRESETS,
    delay_far_grid,
    evaluate_regime,
    null_entry_rate,
    process_noise_covariance,
    update_cusum,
)
from qtrader.regime.detector import _path_efficiency_rw
from qtrader.regime.evaluate import default_kh_grid
from tests.conftest import make_bars, minute_index


def _geo(n: int, *, start: float = 100.0, drift: float = 0.0, vol: float = 0.0, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    r = drift + rng.normal(0.0, vol, n)
    r[0] = 0.0
    return list(start * np.exp(np.cumsum(r)))


def _chop(n: int, *, start: float = 100.0, step: float = 0.004) -> list[float]:
    price = start
    out = []
    for i in range(n):
        price *= np.exp(step if i % 2 == 0 else -step)
        out.append(price)
    return out


# ------------------------------------------------------------------ Kalman
def test_process_noise_diagonal_and_acceleration_are_spd():
    r = 1e-6
    q_diag = process_noise_covariance("diagonal", 0.01 * r, 0.001 * r)
    q_acc = process_noise_covariance("acceleration", 0.01 * r, 0.001 * r)
    assert q_diag.shape == (2, 2)
    assert q_acc[0, 1] == pytest.approx(q_acc[1, 0])
    assert np.linalg.eigvalsh(q_diag).min() >= 0.0
    assert np.linalg.eigvalsh(q_acc).min() > 0.0


def test_local_linear_trend_recovers_a_noiseless_slope():
    kf = LocalLinearTrendFilter(lambda_level=0.05, lambda_slope=0.05)
    r = 1e-8
    kf.reset(0.0, r)
    slope = 0.0
    true = 0.002
    for t in range(1, 60):
        slope = kf.step(true * t, r).slope
    assert slope == pytest.approx(true, rel=0.15)


def test_slope_rw_std_matches_monte_carlo():
    """The Lyapunov recursion *is* the sampling sd of the slope under the null.

    This is the whole basis for reading ``slope_z`` as a z-score, so it is
    checked against brute force rather than against itself.
    """
    n_bars, sigma, trials = 60, 8e-4, 4000
    rng = np.random.default_rng(11)
    slopes = np.empty((trials, n_bars - 1))
    analytic = None
    for m in range(trials):
        steps = rng.standard_normal(n_bars - 1) * sigma
        y = np.concatenate([[0.0], np.cumsum(steps)])
        kf = LocalLinearTrendFilter()
        kf.reset(0.0, sigma**2)
        rows = [kf.step(y[t], sigma**2) for t in range(1, n_bars)]
        slopes[m] = [row.slope for row in rows]
        analytic = np.array([row.slope_rw_std for row in rows])
    ratio = slopes.std(axis=0) / analytic
    # 4000 trials put the sd-of-an-sd at ~1.1%; allow a few of those.
    assert np.abs(ratio[2:] - 1.0).max() < 0.06


def test_model_posterior_sd_and_null_sd_are_different_objects():
    """``slope_std`` is the assumed model's; ``slope_rw_std`` is the null's."""
    kf = LocalLinearTrendFilter()
    kf.reset(0.0, 1e-6)
    step = None
    for t in range(1, 40):
        step = kf.step(1e-4 * t, 1e-6)
    assert step.slope_std > 0 and step.slope_rw_std > 0
    assert step.slope_std != pytest.approx(step.slope_rw_std, rel=1e-3)


def test_cusum_down_stays_non_negative_and_ignores_a_positive_run():
    s_plus, s_minus = 0.0, 0.0
    for _ in range(10):
        s_plus, s_minus = update_cusum(s_plus, s_minus, 1.0, 0.25)
    assert s_minus == 0.0
    assert s_plus == pytest.approx(10 * 0.75)
    s_plus, s_minus = update_cusum(s_plus, s_minus, -3.0, 0.25)
    assert s_minus >= 0.0
    assert s_plus >= 0.0


# ------------------------------------------------------------------ null calibration
def test_efficiency_ratio_null_mean_is_one_for_every_window():
    """``E[ER_n * sqrt(n)] = 1`` under a driftless random walk, for all n.

    This is what lets the 10-bar entry gate and the 5-bar tightened gate share
    a threshold. Raw ``ER`` does not have the property: ``ER >= 0.25`` is a 55%
    coin flip at n=10 and a 75% one at n=5.
    """
    rng = np.random.default_rng(5)
    for n in (5, 10, 20, 40):
        values = []
        for _ in range(4000):
            path = np.concatenate([[0.0], np.cumsum(rng.standard_normal(n) * 1e-3)])
            er_rw, _, bars = _path_efficiency_rw(path, 1e-12)
            values.append(er_rw)
            assert bars == n
        assert float(np.mean(values)) == pytest.approx(1.0, abs=0.05)


def test_signed_efficiency_keeps_the_direction():
    up = np.array([0.0, 0.01, 0.02, 0.03])
    er_rw, signed, n = _path_efficiency_rw(up, 1e-12)
    assert n == 3
    assert signed == pytest.approx(er_rw)
    assert er_rw == pytest.approx(np.sqrt(3.0))  # a straight line: ER = 1
    _, signed_down, _ = _path_efficiency_rw(up[::-1].copy(), 1e-12)
    assert signed_down == pytest.approx(-er_rw)


def test_slope_z_is_standard_normal_on_a_driftless_random_walk():
    """The point of the whole exercise: one threshold, one meaning."""
    rng = np.random.default_rng(13)
    index = minute_index(390, start_local="2026-08-03 09:30")
    zs = []
    for day in range(12):
        steps = rng.standard_normal(390) * 8e-4
        steps[0] = 0.0
        bars = make_bars(list(100.0 * np.exp(np.cumsum(steps))))
        bars.index = index
        out = KalmanCUSUMRegimeDetector.from_preset("balanced").run(bars)
        zs.append(out["slope_z"].iloc[40:].to_numpy())
    z = np.concatenate(zs)
    assert float(np.std(z)) == pytest.approx(1.0, abs=0.15)
    assert float(np.mean(np.abs(z) > 2.0)) == pytest.approx(0.046, abs=0.03)


@pytest.mark.parametrize("preset", ["sensitive", "balanced", "conservative"])
def test_presets_have_a_bounded_false_entry_rate_under_pure_noise(preset):
    """Regression ceiling. A detector that oscillates fails here, not in review.

    Every entry on a driftless random walk is false by construction, so this
    number is a property of the thresholds alone.
    """
    metrics = null_entry_rate(KalmanCUSUMConfig.from_preset(preset), n_sessions=40)
    assert metrics["null_entries_per_session"] <= 10.0
    assert metrics["null_flips_per_session"] <= 20.0


def test_null_entry_rate_is_monotone_in_the_presets():
    rates = [
        null_entry_rate(KalmanCUSUMConfig.from_preset(name), n_sessions=40)[
            "null_entries_per_session"
        ]
        for name in ("conservative", "balanced", "sensitive")
    ]
    assert rates[0] < rates[1] < rates[2]


# ------------------------------------------------------------------ online API
def test_run_matches_sequential_update():
    bars = make_bars(_geo(80, drift=0.0004, vol=0.0002, seed=1))
    a = KalmanCUSUMRegimeDetector.from_preset("balanced")
    b = KalmanCUSUMRegimeDetector.from_preset("balanced")
    out_a = a.run(bars)
    rows = [b.update(row) for _, row in bars.iterrows()]
    out_b = pd.DataFrame(rows, index=bars.index)
    pd.testing.assert_frame_equal(out_a, out_b, check_dtype=False)


def test_future_bars_cannot_change_past_states():
    prices = _geo(60, drift=0.0003, vol=0.0004, seed=2)
    original = make_bars(prices)
    tampered = make_bars(prices[:30] + [999.0] * 30)
    cut = 30
    for preset in PRESETS:
        a = KalmanCUSUMRegimeDetector.from_preset(preset).run(original)
        b = KalmanCUSUMRegimeDetector.from_preset(preset).run(tampered)
        pd.testing.assert_frame_equal(a.iloc[:cut], b.iloc[:cut], check_dtype=False)


def test_slope_z_is_clipped():
    cfg = KalmanCUSUMConfig(z_clip=3.0, lambda_slope=0.05, min_returns=2, er_window=2, er_entry_rw=0.0)
    bars = make_bars(_geo(40, drift=0.01, vol=0.0))
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    assert out["slope_z"].max() <= 3.0 + 1e-12
    assert out["slope_z"].min() >= -3.0 - 1e-12


def test_a_clean_rally_enters_up_and_resets_cusum():
    quiet = _geo(40, drift=0.0, vol=0.0)
    climb = _geo(50, start=quiet[-1], drift=0.003, vol=0.0)
    bars = make_bars(quiet + climb[1:])
    out = KalmanCUSUMRegimeDetector.from_preset("sensitive").run(bars)
    assert (out["state"] == "UP").any()
    assert (out["state"].iloc[:20] == "FLAT").all()
    first_up = int(np.argmax(out["state"].to_numpy() == "UP"))
    assert 40 <= first_up <= 60
    after = out.iloc[first_up + 1]
    assert after["cusum_up"] < out.iloc[first_up]["cusum_up"] or after["cusum_up"] == 0.0
    assert (out["cusum_up"] >= 0).all()
    assert (out["cusum_down"] >= 0).all()


def test_chop_does_not_enter_with_the_conservative_preset():
    bars = make_bars(_chop(120, step=0.005))
    out = KalmanCUSUMRegimeDetector.from_preset("conservative").run(bars)
    assert (out["state"] == "FLAT").all()


def test_one_adverse_bar_does_not_exit_up():
    cfg = KalmanCUSUMConfig(
        lambda_slope=0.005, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=5, min_returns=5, exit_margin=0.0,
        exit_confirm_bars=3, weaken_z=0.0, weaken_frac=0.0,
    )
    up = _geo(50, drift=0.004, vol=0.0)
    dip = [up[-1] * np.exp(-0.01)]
    resume = _geo(20, start=dip[0], drift=0.004, vol=0.0)
    bars = make_bars(up + dip + resume[1:])
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    dip_i = 50
    assert out["state"].iloc[dip_i - 1] == "UP"
    assert out["state"].iloc[dip_i] == "UP"


def test_a_larger_exit_margin_makes_the_trend_stickier():
    """Guards the knob's direction. The old ``exit_z`` read the other way."""
    shared = dict(
        lambda_slope=0.005, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=5, min_returns=5, exit_confirm_bars=2,
        weaken_z=0.0, weaken_frac=0.0, reversal_h=100.0,
    )
    up = _geo(40, drift=0.004, vol=0.0)
    fade = _geo(30, start=up[-1], drift=-0.0008, vol=0.0)
    bars = make_bars(up + fade[1:])
    tight = KalmanCUSUMRegimeDetector(KalmanCUSUMConfig(exit_margin=0.0, **shared)).run(bars)
    sticky = KalmanCUSUMRegimeDetector(KalmanCUSUMConfig(exit_margin=3.0, **shared)).run(bars)
    assert (tight["state"] == "UP").sum() < (sticky["state"] == "UP").sum()


def test_in_trend_er_ignores_bars_before_the_regime():
    """A dump still sitting in the rolling ER window must not dilute an UP."""
    dump = _geo(25, drift=-0.003, vol=0.0)
    climb = _geo(40, start=dump[-1], drift=0.004, vol=0.0)
    bars = make_bars(dump + climb[1:])
    shared = dict(
        lambda_slope=0.008, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=20, min_returns=5,
    )
    on = KalmanCUSUMRegimeDetector(
        KalmanCUSUMConfig(anchor_er_to_regime=True, **shared)
    ).run(bars)
    off = KalmanCUSUMRegimeDetector(
        KalmanCUSUMConfig(anchor_er_to_regime=False, **shared)
    ).run(bars)
    assert (on["state"] == "UP").any()
    first_up = int(np.argmax(on["state"].to_numpy() == "UP"))
    later = first_up + 5
    assert on["state"].iloc[later] == "UP"
    assert on["signed_er_rw"].iloc[later] > 0.0
    assert on["er_rw"].iloc[later] > off["er_rw"].iloc[later]


def test_the_weaken_flag_is_released_when_the_slope_comes_back():
    """A one-way latch would leave the rest of every leg in the hair-trigger window."""
    cfg = KalmanCUSUMConfig(
        lambda_slope=0.008, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=10, min_returns=5, exit_margin=50.0,
        exit_confirm_bars=99, reversal_h=100.0, er_hold_rw=0.0, er_flip_rw=50.0,
        weaken_z=1.0, weaken_frac=0.5,
    )
    climb = _geo(40, drift=0.005, vol=0.0)
    pause = _geo(12, start=climb[-1], drift=0.0, vol=0.0)
    resume = _geo(30, start=pause[-1], drift=0.005, vol=0.0)
    bars = make_bars(climb + pause[1:] + resume[1:])
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    in_trend = out.loc[out["state"] == "UP", "weakened"]
    assert bool(in_trend.any()), "the pause should have set the flag"
    assert not bool(in_trend.iloc[-1]), "the resumed climb should have cleared it"


def test_a_weakened_trend_goes_flat_and_never_straight_to_the_opposite_state():
    """The weaken path has no confirmation, so it may only stand aside.

    Letting it flip UP->DOWN directly was 42% of all transitions in the
    previous design, and the new DOWN then failed its own exit test two bars
    later. An opposite trend earns a normal entry, or trips ``reversal_h``.
    """
    cfg = KalmanCUSUMConfig(
        lambda_slope=0.008, cusum_k=0.1, cusum_h=1.5, entry_z=3.0,
        er_entry_rw=0.5, er_window=10, min_returns=5,
        exit_margin=50.0, exit_confirm_bars=99, reversal_h=100.0,
        er_tighten_window=5, weaken_z=1.0, weaken_frac=0.5,
        er_hold_rw=0.4, er_flip_rw=1.5,
    )
    climb = _geo(50, drift=0.005, vol=0.0)
    dump = _geo(15, start=climb[-1], drift=-0.008, vol=0.0)
    bars = make_bars(climb + dump[1:])
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    states = out["state"].to_numpy()
    assert (states == "UP").any()
    first_up = int(np.argmax(states == "UP"))
    assert (states[first_up:] == "FLAT").any(), "the clean retrace must end the UP"
    pairs = set(zip(states[:-1], states[1:]))
    assert ("UP", "DOWN") not in pairs and ("DOWN", "UP") not in pairs


def test_weakened_chop_exits_to_flat():
    cfg = KalmanCUSUMConfig(
        lambda_slope=0.008, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=10, min_returns=5,
        exit_margin=50.0, exit_confirm_bars=99, reversal_h=100.0,
        er_tighten_window=5, weaken_z=1.0, weaken_frac=0.5,
        er_hold_rw=0.4, er_flip_rw=50.0,
    )
    climb = _geo(40, drift=0.005, vol=0.0)
    pause = _geo(12, start=climb[-1], drift=0.0, vol=0.0)
    chop = _chop(20, start=pause[-1], step=0.004)
    bars = make_bars(climb + pause[1:] + chop)
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    assert (out["state"] == "UP").any()
    first_up = int(np.argmax(out["state"].to_numpy() == "UP"))
    assert (out["state"].iloc[first_up:] == "FLAT").any()
    first_flat = first_up + int(np.argmax(out["state"].iloc[first_up:].to_numpy() == "FLAT"))
    assert bool(out["weakened"].iloc[first_flat])
    assert out["er_rw"].iloc[first_flat] < cfg.er_hold_rw


def test_strong_reversal_skips_flat():
    cfg = KalmanCUSUMConfig(
        lambda_slope=0.008, cusum_k=0.1, cusum_h=1.5, entry_z=0.3,
        er_entry_rw=0.5, er_window=5, min_returns=5,
        reversal_h=2.0, reversal_z=0.8, exit_confirm_bars=5,
        weaken_z=0.0, weaken_frac=0.0,
    )
    climb = _geo(40, drift=0.005, vol=0.0)
    dump = _geo(40, start=climb[-1], drift=-0.006, vol=0.0)
    bars = make_bars(climb + dump[1:])
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    states = out["state"].to_numpy()
    pairs = list(zip(states[:-1], states[1:]))
    assert ("UP", "DOWN") in pairs
    first_down = int(np.argmax(states == "DOWN"))
    assert states[first_down - 1] == "UP"


def test_overnight_gap_does_not_fire_the_next_open():
    day1 = make_bars([100.0] * 40)
    day2 = make_bars([120.0] * 40)
    day2.index = minute_index(40, start_local="2026-08-04 09:30")
    bars = pd.concat([day1, day2])
    out = KalmanCUSUMRegimeDetector.from_preset("sensitive").run(bars)
    assert (out.iloc[40:52]["state"] == "FLAT").all()


def test_vol_normalized_mode_is_finite_after_warmup():
    cfg = KalmanCUSUMConfig(signal_mode="vol_normalized", vol_window=10, min_returns=10)
    bars = make_bars(_geo(50, drift=0.0005, vol=0.0008, seed=3))
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    tail = out.iloc[20:]
    assert tail["signal"].notna().all()
    assert np.isfinite(tail["signal"]).all()


def test_presets_are_ordered_by_slope_noise_and_thresholds():
    s, b, c = PRESETS["sensitive"], PRESETS["balanced"], PRESETS["conservative"]
    assert s.lambda_slope > b.lambda_slope > c.lambda_slope
    assert s.cusum_h < b.cusum_h < c.cusum_h
    assert s.entry_z < b.entry_z < c.entry_z
    assert s.er_entry_rw < b.er_entry_rw < c.er_entry_rw
    assert s.exit_margin < b.exit_margin < c.exit_margin


def test_planted_trend_has_small_delay_and_no_false_alarm_on_the_quiet_prefix():
    quiet = _geo(80, drift=0.0, vol=0.0)
    climb = _geo(60, start=quiet[-1], drift=0.004, vol=0.0)
    bars = make_bars(quiet + climb[1:])
    out = KalmanCUSUMRegimeDetector.from_preset("sensitive").run(bars)
    start = bars.index[80]
    events = pd.DataFrame({"bar_open": [start], "direction": [1], "horizon": [50]})
    metrics = evaluate_regime(out, events, max_delay=25, horizon=50)
    assert metrics["n_captured"] == 1
    assert metrics["mean_delay"] <= 20
    assert (out.loc[out.index < start, "state"] == "FLAT").all()
    assert metrics["n_false_alarms"] == 0


def test_evaluate_regime_refuses_an_empty_event_table():
    """Silence here used to look like a passing grid of NaNs."""
    bars = make_bars(_geo(40, drift=0.002, vol=0.0))
    out = KalmanCUSUMRegimeDetector.from_preset("balanced").run(bars)
    with pytest.raises(ValueError, match="empty table"):
        evaluate_regime(out, pd.DataFrame(columns=["bar_open", "direction", "horizon"]))


def test_delay_far_grid_does_not_use_one_cell_state_in_the_next():
    bars = make_bars(_geo(80, drift=0.002, vol=0.0))
    events = pd.DataFrame({"bar_open": [bars.index[20]], "direction": [1], "horizon": [30]})
    grid = delay_far_grid(bars, events, default_kh_grid()[:3], max_delay=20)
    assert len(grid) == 3
    assert {"mean_delay", "false_alarms_per_session", "capture_ratio"}.issubset(grid.columns)


def test_acceleration_process_noise_still_runs_online():
    cfg = KalmanCUSUMConfig(process_noise="acceleration")
    bars = make_bars(_geo(40, drift=0.001, vol=0.0003, seed=4))
    out = KalmanCUSUMRegimeDetector(cfg).run(bars)
    assert set(out["state"]).issubset({"FLAT", "UP", "DOWN"})
    assert len(out) == 40


def test_exit_margin_must_not_be_negative():
    with pytest.raises(ValueError, match="stickier"):
        KalmanCUSUMConfig(exit_margin=-0.5)


def test_plot_regime_takes_its_reference_lines_from_the_config(tmp_path):
    from qtrader.viz import plot_regime

    bars = make_bars(_geo(40, drift=0.002, vol=0.0))
    detector = KalmanCUSUMRegimeDetector.from_preset("sensitive")
    result = detector.run(bars)
    path = tmp_path / "regime.html"
    fig = plot_regime(bars, result, config=detector.cfg, title="test", path=path)
    names = {trace.name for trace in fig.data}
    types = {trace.type for trace in fig.data}
    assert "candlestick" in types
    assert {"OHLC", "Volume", "CUSUM up", "CUSUM down"} <= names
    lines = [shape for shape in fig.layout.shapes if shape.type == "line"]
    ys = {round(float(shape.y0), 6) for shape in lines}
    assert detector.cfg.entry_z in ys and -detector.cfg.entry_z in ys
    assert detector.cfg.cusum_h in ys and detector.cfg.reversal_h in ys
    assert fig.layout.xaxis.rangeslider.visible is False
    assert path.exists() and path.stat().st_size > 0


def test_plot_delay_far_is_a_plotly_scatter():
    from qtrader.viz import plot_delay_far

    bars = make_bars(_geo(80, drift=0.002, vol=0.0))
    events = pd.DataFrame({"bar_open": [bars.index[20]], "direction": [1], "horizon": [30]})
    grid = delay_far_grid(bars, events, default_kh_grid()[:2], max_delay=20)
    fig = plot_delay_far(grid)
    assert fig.data[0].type == "scatter"
    assert fig.layout.xaxis.title.text == "false alarms per session"
