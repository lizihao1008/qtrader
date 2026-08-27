"""Trend-ratchet: entry authorisation, risk sizing, and the monotone barrier."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.strategies.trend_ratchet import TrendRatchetStrategy
from tests.conftest import make_context

CALM = dict(
    macd_fast=3, macd_slow=8, macd_signal=3,
    trend_span=10, trend_window=10, vol_window=20, horizon_bars=10,
    no_entry_after=None, flat_time=None,
)


def context_from(path, *, extra=None):
    prices = {"AAA": list(path), "SPY": [400.0] * len(path)}
    prices.update(extra or {})
    return make_context(prices, symbols=[s for s in prices if s != "SPY"])


def weights_of(strategy, context, symbol="AAA") -> pd.Series:
    return strategy.generate(context).target_weights[symbol]


# ------------------------------------------------------------- configuration
def test_a_trailing_stop_tighter_than_the_initial_one_is_rejected():
    with pytest.raises(ValueError, match="trail_sigmas"):
        TrendRatchetStrategy(stop_sigmas=2.0, trail_sigmas=1.0)


def test_a_book_that_cannot_fit_its_own_positions_is_rejected():
    with pytest.raises(ValueError, match="exceeds the gross budget"):
        TrendRatchetStrategy(max_positions=10, max_weight=0.2)


def test_a_reset_level_at_or_above_the_entry_level_is_rejected():
    with pytest.raises(ValueError, match="trend_z_reset"):
        TrendRatchetStrategy(trend_z_min=2.0, trend_z_reset=2.0)


def test_a_non_positive_significance_threshold_is_rejected():
    with pytest.raises(ValueError, match="trend_z_min"):
        TrendRatchetStrategy(trend_z_min=0.0)


def test_an_unknown_trend_estimator_is_rejected():
    with pytest.raises(ValueError, match="trend_estimator"):
        TrendRatchetStrategy(trend_estimator="eyeball")


def test_a_negative_crossing_threshold_is_rejected():
    with pytest.raises(ValueError, match="min_cross_zscore"):
        TrendRatchetStrategy(min_cross_zscore=-1.0)


# ------------------------------------------------- estimators and the crossing gate
def test_the_ewma_estimator_has_an_opinion_from_the_start_of_the_session():
    """The point of it: no hour-long blackout at the open."""
    rng = np.random.default_rng(20)
    path = 100.0 * np.exp(np.cumsum(rng.normal(0.0008, 0.0006, 300)))
    context = context_from(path)

    settings = {**CALM, "trend_z_min": 1.2, "vol_window": 20}
    ewma = TrendRatchetStrategy(trend_estimator="ewma", **settings).generate(context)
    window = TrendRatchetStrategy(trend_estimator="window", **settings).generate(context)

    ewma_z = ewma.indicators["AAA"]["trend_zscore"]
    window_z = window.indicators["AAA"]["trend_zscore"]

    # The window estimator is blind until it is full; the EWMA is not.
    assert window_z.iloc[: CALM["trend_window"] - 1].isna().all()
    assert ewma_z.iloc[2:20].notna().any()


def test_requiring_the_oscillator_to_accelerate_reduces_trading():
    rng = np.random.default_rng(21)
    path = 100.0 * np.exp(np.cumsum(rng.normal(0.0008, 0.0008, 400)))
    context = context_from(path)
    settings = {**CALM, "trend_z_min": 1.0, "trend_z_reset": 0.5}

    loose = TrendRatchetStrategy(min_cross_zscore=0.0, **settings)
    strict = TrendRatchetStrategy(min_cross_zscore=3.0, **settings)

    assert (weights_of(strict, context) != 0).sum() < (weights_of(loose, context) != 0).sum()


# --------------------------------------------------- the state entry and its hysteresis
def test_a_sustained_trend_is_entered_without_waiting_for_a_crossing():
    """The flaw the state entry exists to fix: a slow trend need not cross.

    A trend that unfolds over hours can be significant for most of its length
    while the oscillator crosses only at its edges. An event-triggered entry
    watches it go by; a state-triggered one is in it.
    """
    rng = np.random.default_rng(40)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0010, 0.0005, 400)))
    context = context_from(ramp)
    strategy = TrendRatchetStrategy(
        trend_estimator="session", trend_z_min=2.0, trend_z_reset=1.0, **CALM
    )

    weights = weights_of(strategy, context)
    assert (weights > 0).sum() > 100  # held through the move, not dipped into


def test_the_position_survives_histogram_flips_while_the_trend_holds():
    """A sustained trend flips the histogram repeatedly; that must not close it."""
    rng = np.random.default_rng(41)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0010, 0.0005, 400)))
    context = context_from(ramp)
    settings = dict(trend_estimator="session", trend_z_min=2.0, trend_z_reset=1.0, **CALM)

    patient = TrendRatchetStrategy(exit_on_opposite_cross=False, **settings)
    twitchy = TrendRatchetStrategy(exit_on_opposite_cross=True, **settings)

    held = (weights_of(patient, context) != 0).sum()
    chopped = (weights_of(twitchy, context) != 0).sum()
    assert held > chopped


def test_hysteresis_prevents_re_entry_until_the_trend_has_lapsed():
    """Without it, a stop inside a live trend is followed by an instant re-entry."""
    rng = np.random.default_rng(42)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0010, 0.0006, 400)))
    context = context_from(ramp)
    settings = dict(trend_estimator="session", trend_z_min=2.0, **CALM)

    strict = TrendRatchetStrategy(trend_z_reset=1.9, **settings)
    loose = TrendRatchetStrategy(trend_z_reset=0.1, **settings)

    def entries(strategy):
        w = weights_of(strategy, context).to_numpy()
        return int(((w != 0) & (np.r_[0.0, w[:-1]] == 0)).sum())

    assert entries(strict) <= entries(loose)


def test_the_trend_statistic_releases_a_position_when_it_changes_sides():
    """Up hard enough to be bought, then down hard enough that the case has gone."""
    up = 100.0 * np.exp(np.cumsum(np.r_[0.0, np.full(120, 0.0016)]))
    down = up[-1] * np.exp(np.cumsum(np.full(200, -0.0016)))
    context = context_from(np.concatenate([up, down]))
    strategy = TrendRatchetStrategy(
        trend_estimator="session", trend_z_min=2.0, trend_z_reset=1.0,
        exit_on_opposite_cross=False, **CALM
    )

    weights = weights_of(strategy, context).to_numpy()
    assert (weights[:121] > 0).any()  # was long into the top
    assert weights[-1] <= 0  # and is not still long at the end


# -------------------------------------------------------------------- entries
def test_a_flat_market_is_traded_far_less_than_a_trending_one():
    """The filter is a significance test, not a promise of silence.

    A calibrated statistic crosses 2.5 sigma on noise about 1% of the time by
    construction, so "never trades a random walk" would be the wrong assertion —
    it would only hold for a filter that had been made too strict to be useful.
    What must hold is that drift changes the answer.
    """
    rng = np.random.default_rng(3)
    chop = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0005, 300)))
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0012, 0.0005, 300)))
    strategy = TrendRatchetStrategy(trend_z_min=2.5, **CALM)

    in_chop = (weights_of(strategy, context_from(chop)) != 0).sum()
    in_trend = (weights_of(strategy, context_from(ramp)) != 0).sum()
    assert in_trend > 3 * max(in_chop, 1)


def test_a_steady_uptrend_is_bought():
    rng = np.random.default_rng(4)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0012, 0.0006, 300)))
    strategy = TrendRatchetStrategy(trend_z_min=1.5, **CALM)

    weights = weights_of(strategy, context_from(ramp))
    assert (weights > 0).any()
    assert not (weights < 0).any()


def test_shorting_can_be_switched_off():
    rng = np.random.default_rng(5)
    decline = 100.0 * np.exp(np.cumsum(rng.normal(-0.0012, 0.0006, 300)))
    common = dict(trend_z_min=1.5, **CALM)

    assert (weights_of(TrendRatchetStrategy(allow_short=True, **common),
                       context_from(decline)) < 0).any()
    assert (weights_of(TrendRatchetStrategy(allow_short=False, **common),
                       context_from(decline)) >= 0).all()


def test_the_book_never_exceeds_its_position_limit():
    rng = np.random.default_rng(6)
    paths = {
        f"S{i}": list(100.0 * np.exp(np.cumsum(rng.normal(0.001, 0.0008, 300))))
        for i in range(8)
    }
    paths["SPY"] = [400.0] * 300
    context = make_context(paths, symbols=[f"S{i}" for i in range(8)])

    strategy = TrendRatchetStrategy(
        max_positions=3, max_weight=0.3, trend_z_min=1.0, trend_z_reset=0.5, **CALM
    )
    weights = strategy.generate(context).target_weights
    assert (weights.ne(0).sum(axis=1) <= 3).all()
    assert weights.abs().sum(axis=1).max() <= 1.0 + 1e-9


# -------------------------------------------------------------------- sizing
def test_a_more_volatile_symbol_gets_a_smaller_position():
    """Fixed-fractional risk: weight x stop distance is the constant, not weight."""
    strategy = TrendRatchetStrategy(
        risk_per_trade=0.001, max_weight=1.0, max_positions=1, stop_sigmas=1.0
    )

    calm = strategy._entry_weight(np.array([0.005]))[0]
    wild = strategy._entry_weight(np.array([0.020]))[0]

    assert wild == pytest.approx(calm / 4.0)
    assert calm * 0.005 == pytest.approx(0.001)  # risked fraction is the invariant


def test_position_size_is_capped():
    strategy = TrendRatchetStrategy(risk_per_trade=0.05, max_weight=0.15)
    assert strategy._entry_weight(np.array([0.001]))[0] == pytest.approx(0.15)


def test_an_unknown_volatility_produces_no_position():
    strategy = TrendRatchetStrategy()
    assert strategy._entry_weight(np.array([np.nan]))[0] == 0.0


# -------------------------------------------------------------------- barrier
def _held_weights(path, **overrides) -> pd.Series:
    settings = {**CALM, "trend_z_min": 1.2, **overrides}
    strategy = TrendRatchetStrategy(**settings)
    return weights_of(strategy, context_from(path))


def test_an_immediate_adverse_move_is_stopped_out():
    """Up sharply to trigger an entry, then straight down through the stop."""
    rng = np.random.default_rng(8)
    rise = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 220)))
    collapse = rise[-1] * np.exp(np.cumsum(np.full(60, -0.004)))
    weights = _held_weights(np.concatenate([rise, collapse]), stop_sigmas=1.0, trail_sigmas=1.5)

    entered = weights.to_numpy() > 0
    assert entered.any()
    # The position is gone well before the collapse ends.
    assert not entered[-20:].any()


def test_a_position_that_keeps_winning_is_kept():
    rng = np.random.default_rng(9)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 320)))
    weights = _held_weights(ramp)

    held = weights.to_numpy() > 0
    assert held.any()
    # A trend that never retraces past the ratchet is still on at the end.
    assert held[-1]


def test_profit_is_taken_when_the_retracement_exceeds_the_trail():
    """Rise far enough to arm the ratchet, then give a chunk back."""
    rng = np.random.default_rng(10)
    rise = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 260)))
    peak = rise[-1]
    giveback = peak * np.exp(np.cumsum(np.full(40, -0.0015)))
    weights = _held_weights(np.concatenate([rise, giveback]), stop_sigmas=1.0, trail_sigmas=1.2)

    values = weights.to_numpy()
    assert (values[:260] > 0).any()  # was long into the peak
    assert values[-1] == 0.0  # and out again after the retracement


def test_a_wider_trail_holds_a_retracement_that_a_tighter_one_exits():
    """Isolate the barrier: the momentum exit is switched off so only the trail acts."""
    rng = np.random.default_rng(12)
    rise = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 260)))
    giveback = rise[-1] * np.exp(np.cumsum(np.full(25, -0.0012)))
    path = np.concatenate([rise, giveback])

    settings = dict(stop_sigmas=1.0, exit_on_opposite_cross=False)
    tight = _held_weights(path, trail_sigmas=1.0, **settings).to_numpy()
    wide = _held_weights(path, trail_sigmas=8.0, **settings).to_numpy()

    assert (wide != 0).sum() > (tight != 0).sum()


def test_the_position_is_flat_before_the_close():
    rng = np.random.default_rng(13)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 390)))
    context = context_from(ramp)
    strategy = TrendRatchetStrategy(**{**CALM, "flat_time": "15:50", "trend_z_min": 1.2})
    weights = strategy.generate(context).target_weights["AAA"]

    late = weights.index.tz_convert("America/New_York").time >= pd.Timestamp("15:50").time()
    assert (weights[late] == 0).all()


def test_no_entry_is_opened_without_a_full_session_window_behind_it():
    """A trend window straddling the overnight gap would read the gap as drift."""
    rng = np.random.default_rng(14)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 300)))
    strategy = TrendRatchetStrategy(**{**CALM, "trend_z_min": 1.2})

    weights = weights_of(strategy, context_from(ramp))
    assert (weights.iloc[: strategy.trend_window - 1] == 0).all()


def test_setup_features_are_exposed_for_post_trade_analysis():
    rng = np.random.default_rng(15)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0015, 0.0004, 300)))
    context = context_from(ramp)
    strategy = TrendRatchetStrategy(**{**CALM, "trend_z_min": 1.2})

    signals = strategy.generate(context)
    features = strategy.setup_features(signals, context)
    assert {
        "trend_zscore", "abs_trend_zscore", "cross_zscore", "abs_cross_zscore",
        "horizon_sigma_bps", "macd_hist_bps",
    } == set(features)
    assert (features["abs_trend_zscore"].dropna() >= 0).all().all()
    assert (features["abs_cross_zscore"].dropna() >= 0).all().all()


def test_a_negative_session_confirmation_level_is_rejected():
    with pytest.raises(ValueError, match="session_confirm_z"):
        TrendRatchetStrategy(session_confirm_z=-1.0)


def test_session_confirmation_blocks_a_fast_signal_the_day_contradicts():
    """A fast trigger firing against the day's own drift must not be taken."""
    # Down all morning, then a sharp bounce: the fast estimator turns positive
    # while the session's drift is still firmly negative.
    down = 100.0 * np.exp(np.cumsum(np.r_[0.0, np.full(200, -0.0012)]))
    bounce = down[-1] * np.exp(np.cumsum(np.full(80, 0.0030)))
    context = context_from(np.concatenate([down, bounce]))

    settings = dict(trend_estimator="ewma", trend_z_min=2.0, trend_z_reset=1.0,
                    allow_short=False, **CALM)
    unconfirmed = TrendRatchetStrategy(session_confirm_z=None, **settings)
    confirmed = TrendRatchetStrategy(session_confirm_z=0.0, **settings)

    assert (weights_of(unconfirmed, context) > 0).any()  # the bounce is bought
    assert not (weights_of(confirmed, context) > 0).any()  # the day says no


def test_session_confirmation_still_allows_a_trade_the_day_agrees_with():
    rng = np.random.default_rng(43)
    ramp = 100.0 * np.exp(np.cumsum(rng.normal(0.0012, 0.0006, 300)))
    context = context_from(ramp)
    strategy = TrendRatchetStrategy(
        trend_estimator="ewma", trend_z_min=2.0, trend_z_reset=1.0,
        session_confirm_z=0.0, **CALM
    )
    assert (weights_of(strategy, context) > 0).any()
