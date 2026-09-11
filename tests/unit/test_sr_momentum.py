"""Break, retest, momentum — and the veto.

The entry is a conjunction of four conditions, so the tests that matter are the
ones that remove one condition at a time and check the entry disappears. The
prefix test is the leakage test: the strategy is a bar loop, and a bar loop that
reads ahead is not visible in its output unless you look for it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from qtrader.strategies.sr_momentum import LONG, SRMomentumStrategy
from qtrader.data.panel import BarPanel
from qtrader.strategies.base import MarketContext
from qtrader.universe.definition import Universe
from tests.conftest import make_bars, make_context, minute_index

WARMUP = 80  # relative volume needs a session of history before it is finite

# Break 100, come back to touch it, hold above, continue.
RETEST = [99.6, 99.7, 99.8, 99.9, 100.4, 100.6, 100.01, 100.5, 101.0, 101.5, 102.0]
# The same move without ever returning to the level.
STRAIGHT = [99.6, 99.7, 99.8, 99.9, 100.4, 100.6, 100.70, 100.5, 101.0, 101.5, 102.0]

PARAMS = dict(
    use_previous_day=False,
    use_opening_range=False,
    use_round_numbers=True,
    vol_window=8,
    atr_window=8,
    rvol_min=0.5,
    require_vwap_side=False,
    momentum_z_min=0.4,
    no_entry_after=None,
    flat_time=None,
)


def warmup(seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(99.0 + np.cumsum(rng.normal(0.0, 0.02, WARMUP)))


def entry_times(frame) -> list[str]:
    """Exchange-local HH:MM of every proposed entry, in order."""
    fired = frame[frame["candidate"] != 0]
    return [t.strftime("%H:%M") for t in fired.index.tz_convert("America/New_York")]


def run(tail: list[float], **overrides):
    path = warmup() + list(tail)
    context = make_context({"AAA": path, "SPY": [400.0] * len(path)})
    strategy = SRMomentumStrategy(**{**PARAMS, **overrides})
    return strategy.generate(context).indicators["AAA"]


# ----------------------------------------------------------------- the entry
def test_a_broken_and_retested_level_is_entered_long():
    candidate = run(RETEST)["candidate"].to_numpy()
    assert (candidate == LONG).sum() == 1
    assert candidate[WARMUP + 7] == LONG, "entry belongs on the bar that holds the level"


def test_a_break_without_a_retest_is_not_entered():
    """The whole premise is the retest. Without it there is no setup."""
    assert (run(STRAIGHT)["candidate"] != 0).sum() == 0


def test_the_round_level_must_be_reachable_from_above():
    """Regression: brackets taken from the current close can never be broken."""
    assert (run(RETEST)["candidate"] != 0).any()


def test_weak_momentum_blocks_the_entry():
    assert (run(RETEST, momentum_z_min=5.0)["candidate"] != 0).sum() == 0


def test_thin_participation_blocks_the_entry():
    assert (run(RETEST, rvol_min=10.0)["candidate"] != 0).sum() == 0


def test_a_stale_break_expires_before_the_retest_counts():
    assert (run(RETEST, retest_bars=1)["candidate"] != 0).sum() == 0


# ------------------------------------------------------- break acceptance
def test_acceptance_waits_for_a_subsequent_close_to_continue_the_break():
    tail = [99.6, 99.7, 99.8, 99.9, 100.4, 100.6, 100.8]
    immediate = run(tail, require_retest=False, acceptance_bars=0)["candidate"].to_numpy()
    accepted = run(tail, require_retest=False, acceptance_bars=1)["candidate"].to_numpy()

    assert immediate[WARMUP + 4] == LONG, "baseline did not enter on the break bar"
    assert accepted[WARMUP + 4] == 0, "the break bar counted as its own confirmation"
    assert accepted[WARMUP + 5] == LONG, "the next higher close was not accepted"


def test_an_adverse_close_after_the_break_is_not_acceptance():
    tail = [99.6, 99.7, 99.8, 99.9, 100.4, 100.2, 100.1]
    candidate = run(
        tail, require_retest=False, acceptance_bars=1
    )["candidate"].to_numpy()

    assert not (candidate[WARMUP + 4 :] != 0).any()


def test_negative_acceptance_bars_are_rejected():
    with pytest.raises(ValueError, match="acceptance_bars"):
        SRMomentumStrategy(**{**PARAMS, "acceptance_bars": -1})


# ------------------------------------------------------------- confirmation
def test_confirmation_can_only_veto_never_create():
    baseline = run(RETEST)["candidate"].to_numpy()
    index = run(RETEST).index

    against = pd.DataFrame(-1.0, index=index, columns=["AAA"])
    vetoed = run(RETEST, confirmation=against, confirmation_min=0.5)["candidate"].to_numpy()
    assert (vetoed != 0).sum() == 0

    agreeing = pd.DataFrame(+1.0, index=index, columns=["AAA"])
    passed = run(RETEST, confirmation=agreeing, confirmation_min=0.5)["candidate"].to_numpy()
    assert np.array_equal(passed, baseline)
    assert set(np.flatnonzero(passed)) <= set(np.flatnonzero(baseline))


def test_an_unscored_candidate_is_not_confirmed():
    """A missing score is a refusal, not a pass. NaN must not become a trade."""
    index = run(RETEST).index
    missing = pd.DataFrame(np.nan, index=index, columns=["AAA"])
    assert (run(RETEST, confirmation=missing, confirmation_min=0.0)["candidate"] != 0).sum() == 0


# ------------------------------------------------------------------ leakage
def test_the_weights_on_a_prefix_match_the_weights_on_the_whole_history():
    """Truncating the future must not change any weight in the past."""
    rng = np.random.default_rng(7)
    path = list(100.0 + np.cumsum(rng.normal(0.0, 0.05, 200)))
    cut = 150

    full = make_context({"AAA": path, "SPY": [400.0] * len(path)})
    prefix = make_context({"AAA": path[:cut], "SPY": [400.0] * cut})

    strategy = SRMomentumStrategy(**PARAMS)
    whole = strategy.generate(full).target_weights["AAA"].to_numpy()[:cut]
    early = strategy.generate(prefix).target_weights["AAA"].to_numpy()

    assert np.allclose(whole, early, equal_nan=True)


# ------------------------------------------------------- session discipline
def test_nothing_is_held_into_the_close():
    rng = np.random.default_rng(3)
    path = list(100.0 + np.cumsum(rng.normal(0.0, 0.05, 390)))
    context = make_context({"AAA": path, "SPY": [400.0] * len(path)})
    weights = SRMomentumStrategy(
        **{**PARAMS, "flat_time": "15:50", "no_entry_after": "15:00"}
    ).generate(context).target_weights

    local = weights.index.tz_convert("America/New_York")
    assert (weights[local.time >= pd.Timestamp("15:50").time()] == 0).all().all()


def test_the_book_never_exceeds_max_positions():
    rng = np.random.default_rng(11)
    paths = {
        f"S{k}": list(100.0 + np.cumsum(rng.normal(0.0, 0.05, 300))) for k in range(8)
    }
    paths["SPY"] = [400.0] * 300
    context = make_context(paths)
    weights = SRMomentumStrategy(
        **{**PARAMS, "max_positions": 3, "max_weight": 0.15}
    ).generate(context).target_weights

    assert (weights != 0).sum(axis=1).max() <= 3
    assert weights.abs().sum(axis=1).max() <= 0.45 + 1e-9


# ------------------------------------------------------- the open blackout
def test_the_open_blackout_suppresses_an_entry_before_its_cutoff():
    """The engineered setup fires at 10:57; the gate is tested either side."""
    assert entry_times(run(RETEST)) == ["10:57"]

    assert entry_times(run(RETEST, no_entry_before="10:00")) == ["10:57"]
    assert entry_times(run(RETEST, no_entry_before="11:05")) == []


def test_a_blocked_setup_is_not_delayed_until_the_gate_opens():
    """A pre-cutoff break is consumed, not queued for the first admitted bar."""
    assert entry_times(run(RETEST, no_entry_before="11:00")) == []


def test_a_consumed_setup_must_reset_before_the_same_level_can_trade_again():
    """Returning through the level and breaking it again creates a new event."""
    reset_and_rebreak = RETEST + [100.4, 99.7, 99.6, 100.5, 101.0, 101.5]
    entries = entry_times(
        run(
            reset_and_rebreak,
            no_entry_before="11:00",
            momentum_z_min=0.1,
            require_retest=False,
        )
    )

    assert entries
    assert entries[0] > "11:00", "the old setup leaked into the cutoff bar"


def test_an_entry_is_never_admitted_before_the_cutoff():
    for cutoff in ("10:00", "10:58", "11:00"):
        for stamp in entry_times(run(RETEST, no_entry_before=cutoff)):
            assert stamp >= cutoff


def test_an_unset_blackout_gates_nothing():
    rng = np.random.default_rng(5)
    path = list(100.0 + np.cumsum(rng.normal(0.0, 0.05, 300)))
    context = make_context({"AAA": path, "SPY": [400.0] * len(path)})

    def candidates(**overrides):
        return SRMomentumStrategy(
            **{**PARAMS, **overrides}
        ).generate(context).indicators["AAA"]["candidate"].to_numpy()

    assert np.array_equal(candidates(), candidates(no_entry_before=None))


def test_intraday_position_state_is_reset_at_a_new_session():
    """An exchange early close may have no configured wall-clock flat bar."""
    first_path = warmup() + RETEST
    second_path = [102.0, 102.1, 102.2]
    index = minute_index(len(first_path), "2026-08-03 09:30").append(
        minute_index(len(second_path), "2026-08-04 09:30")
    )
    panel = BarPanel.from_frames(
        {
            "AAA": make_bars(first_path + second_path, index=index),
            "SPY": make_bars([400.0] * len(index), index=index),
        }
    )
    context = MarketContext(
        panel=panel,
        universe=Universe(name="test", symbols=("AAA",), benchmark="SPY"),
        tradable=pd.DataFrame(True, index=index, columns=["AAA"]),
    )

    weights = SRMomentumStrategy(**PARAMS).generate(context).target_weights["AAA"]

    assert weights.iloc[len(first_path) - 1] != 0, "fixture never opened the position"
    assert weights.iloc[len(first_path)] == 0, "position state crossed the session boundary"


# --------------------------------------------------------- momentum estimator
def test_the_session_estimator_cannot_reverse_after_a_large_early_move():
    """Why the option exists: `session` measures position, not direction.

    A big early drop fixes the sign of `sum(r)/(sigma*sqrt(n))` for the rest of
    the day, so the filter still reads "down" while price is climbing.
    """
    # One synthetic session: drift down hard, then rally back part of the way.
    # Price ends well below where the session started but has risen for 20 bars.
    crash = list(np.linspace(99.0, 90.0, 40)) + list(np.linspace(90.0, 93.0, 20))
    path = warmup() + crash
    context = make_context({"AAA": path, "SPY": [400.0] * len(path)})

    def momentum(estimator):
        frame = SRMomentumStrategy(
            **{**PARAMS, "momentum_estimator": estimator, "momentum_span": 6}
        ).generate(context).indicators["AAA"]
        return frame["momentum_z"].to_numpy()[-1]

    assert momentum("session") < 0, "session drift still reads down"
    assert momentum("ewma") > 0, "ewma follows the rally that is actually happening"


def test_an_unknown_momentum_estimator_is_rejected():
    with pytest.raises(ValueError, match="momentum_estimator"):
        SRMomentumStrategy(momentum_estimator="magic")


# ------------------------------------------------------ the break state machine
def test_a_break_registers_once_and_then_ages():
    """Regression: re-registering every bar collapses the retest into one bar.

    While price stays beyond the level the break must stay live and age, so the
    retest window means what it says. Testing only "price is beyond a level"
    resets the age to 0 and clears the retest flag on every bar.
    """
    # break 100 on the way up, then stay above it without ever coming back
    tail = [99.6, 99.7, 99.8, 99.9] + [100.4 + 0.05 * k for k in range(8)]
    frame = run(tail, retest_bars=99)
    age = frame["watched_age"].to_numpy()[WARMUP + 4 : WARMUP + 12]

    assert age[0] == 0, "the break registers on the bar price clears the level"
    assert list(age[:6]) == [0, 1, 2, 3, 4, 5], "a live break must age, not reset"


def test_a_break_expires_once_the_retest_window_passes():
    tail = [99.6, 99.7, 99.8, 99.9] + [100.4 + 0.05 * k for k in range(8)]
    side = run(tail, retest_bars=2)["watched_side"].to_numpy()[WARMUP + 4 : WARMUP + 12]

    assert side[0] != 0, "break is live when it registers"
    assert side[-1] == 0, "and gone once retest_bars has elapsed with no retest"


# ------------------------------------------------------------------- sizing
def test_risk_sizing_gives_the_tightest_stop_the_largest_position():
    """The rule works as designed — which is the problem, not a bug."""
    strategy = SRMomentumStrategy(**{**PARAMS, "sizing": "risk",
                                     "risk_per_trade": 0.001, "stop_sigmas": 1.0})
    quiet, loud = strategy._entry_weight(np.array([0.002])), strategy._entry_weight(np.array([0.02]))
    assert quiet[0] > loud[0]
    assert quiet[0] == pytest.approx(min(0.001 / 0.002, PARAMS.get("max_weight", 0.15)))


def test_equal_sizing_ignores_volatility_entirely():
    strategy = SRMomentumStrategy(**{**PARAMS, "sizing": "equal", "max_weight": 0.15})
    weights = strategy._entry_weight(np.array([0.002, 0.02, np.nan, 0.0]))
    assert list(weights[:2]) == [0.15, 0.15]
    assert list(weights[2:]) == [0.0, 0.0], "no size without a volatility estimate"


def test_an_unknown_sizing_rule_is_rejected():
    with pytest.raises(ValueError, match="sizing"):
        SRMomentumStrategy(sizing="kelly")


# ---------------------------------------------------------- initial ATR stop
def test_atr_caps_only_the_initial_stop_distance():
    strategy = SRMomentumStrategy(
        **{**PARAMS, "stop_sigmas": 1.0, "initial_stop_atr": 2.0}
    )
    distance = strategy._initial_stop_fraction(
        np.array([0.04, 0.01]),
        np.array([1.0, 1.0]),
        np.array([100.0, 100.0]),
    )

    assert list(distance) == pytest.approx([0.02, 0.01])
    assert strategy.trail_sigmas == 1.5, "the ATR cap changed the trailing rule"


def test_an_unset_atr_stop_preserves_the_sigma_distance():
    strategy = SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 1.25})
    sigma = np.array([0.01, 0.04])

    assert strategy._initial_stop_fraction(
        sigma, np.array([1.0, 1.0]), np.array([100.0, 100.0])
    ) == pytest.approx(1.25 * sigma)


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_non_positive_atr_stop_is_rejected(value):
    with pytest.raises(ValueError, match="initial_stop_atr"):
        SRMomentumStrategy(**{**PARAMS, "initial_stop_atr": value})


def test_a_stopped_trade_cannot_reenter_the_same_unreset_break():
    tail = [99.6, 99.7, 99.8, 99.9, 100.4, 100.3, 100.2, 100.1, 100.05]
    candidate = run(
        tail,
        require_retest=False,
        initial_stop_atr=0.01,
        trail_sigmas=6.0,
    )["candidate"].to_numpy()

    assert (candidate[WARMUP + 4 :] != 0).sum() == 1


# ------------------------------------------------------------- give-back cap
def test_the_giveback_cap_exits_before_a_wide_trail_would():
    """The MU 2026-06-29 case: sigma_H at entry was 280 bps, so the 1.5-sigma
    trail allowed surrendering 420 bps — 77% of a 543 bps peak. The cap is what
    stops a trade that far ahead from giving nearly all of it back."""
    tail = RETEST[:8] + [102.0, 104.0, 106.0, 108.0,
                         106.0, 104.0, 102.5, 101.5, 101.0, 100.8]

    def held(**kw):
        weights = run(tail, **{"trail_sigmas": 6.0, **kw})["target_weight"].to_numpy()
        return int((weights != 0).sum())

    assert held(max_giveback=0.3) < held(), "the cap never bound"


def test_the_cap_cannot_close_a_trade_that_was_never_ahead():
    """Armed only once the position is up more than it risked."""
    losing = RETEST[:8] + [100.4, 100.2, 100.1, 100.0, 99.9, 99.8, 99.7]
    plain = run(losing, trail_sigmas=6.0)["target_weight"].to_numpy()
    capped = run(losing, trail_sigmas=6.0, max_giveback=0.1)["target_weight"].to_numpy()
    assert np.array_equal(plain, capped)


def test_an_unset_cap_leaves_the_ratchet_untouched():
    baseline = run(RETEST)["target_weight"].to_numpy()
    assert np.array_equal(run(RETEST, max_giveback=None)["target_weight"].to_numpy(), baseline)


@pytest.mark.parametrize("value", [0.0, 1.0, -0.2, 1.5])
def test_a_cap_outside_the_open_interval_is_refused(value):
    with pytest.raises(ValueError, match="max_giveback"):
        SRMomentumStrategy(**{**PARAMS, "max_giveback": value})


# --------------------------------------------------- trend-consistency exits
# The ratchet is widened in these two so the reversal is the binding rule;
# with the default trail the barrier closes the position first and the exit
# under test never gets a chance to fire.
REVERSAL_TAIL = RETEST[:8] + [101.0, 102.0, 103.0, 104.0] + [102.0, 100.0, 98.0, 96.0, 94.0]


def test_the_reversal_exit_closes_when_the_trend_statistic_turns():
    def held(**kw):
        weights = run(REVERSAL_TAIL, momentum_estimator="ewma", trail_sigmas=8.0,
                      **kw)["target_weight"].to_numpy()
        return int((weights != 0).sum())

    assert held(exit_on_reversal=0.0) < held(), "the reversal exit never fired"


def test_a_reversal_must_persist_to_count():
    """A z-score crosses zero constantly; one bar on the other side is noise."""
    def held(bars):
        weights = run(REVERSAL_TAIL, momentum_estimator="ewma", trail_sigmas=8.0,
                      exit_on_reversal=0.0, reversal_bars=bars)["target_weight"].to_numpy()
        return int((weights != 0).sum())

    assert held(4) >= held(1), "requiring persistence must not exit sooner"


def test_the_opposite_signal_exit_is_off_by_default():
    baseline = run(RETEST)["target_weight"].to_numpy()
    assert np.array_equal(
        run(RETEST, exit_on_opposite_signal=False)["target_weight"].to_numpy(), baseline
    )


def test_enabling_the_opposite_signal_exit_only_removes_exposure():
    rng = np.random.default_rng(9)
    path = list(100.0 + np.cumsum(rng.normal(0.0, 0.06, 300)))
    context = make_context({"AAA": path, "SPY": [400.0] * len(path)})

    def weights(**kw):
        return SRMomentumStrategy(
            **{**PARAMS, "momentum_estimator": "ewma", **kw}
        ).generate(context).target_weights["AAA"].to_numpy()

    plain, exiting = weights(), weights(exit_on_opposite_signal=True)
    assert (np.abs(exiting) <= np.abs(plain) + 1e-12).all(), "exposure grew"


@pytest.mark.parametrize("kwargs", [
    {"exit_on_reversal": -0.1},
    {"reversal_bars": 0},
])
def test_invalid_exit_parameters_are_refused(kwargs):
    with pytest.raises(ValueError):
        SRMomentumStrategy(**{**PARAMS, **kwargs})


# --------------------------------------------------------------- displacement
def book(strategy, opening, position, strength, losing):
    return strategy._resolve_book(
        np.array(opening, dtype=bool), np.array(position),
        np.array(strength, dtype=float), np.array(losing, dtype=bool),
    )


def displacing(**kw):
    return SRMomentumStrategy(**{**PARAMS, "max_positions": 2,
                                 "max_weight": 0.4, **kw})


def test_free_slots_are_filled_strongest_first():
    s = displacing(displace_margin=1.0)
    opening, displaced = book(s, [1, 1, 1], [0, 0, 0], [0.2, 2.0, 1.0], [0, 0, 0])
    assert list(opening) == [False, True, True]
    assert not displaced.any()


def test_a_full_book_admits_nothing_without_a_margin():
    """The behaviour R17 measured: 83% of bars, ranking never runs."""
    s = displacing(displace_margin=None)
    opening, displaced = book(s, [1, 0, 0], [0, 1, -1], [3.0, 0.1, 0.1], [0, 1, 1])
    assert not opening.any() and not displaced.any()


def test_a_much_stronger_candidate_replaces_a_losing_holding():
    s = displacing(displace_margin=1.0)
    opening, displaced = book(s, [1, 0, 0], [0, 1, -1], [3.0, 0.1, 2.0], [0, 1, 0])
    assert opening[0] and displaced[1], "the weak losing holding should go"
    assert not displaced[2], "the strong holding must survive"
    assert opening.sum() == displaced.sum(), "the book grew"


def test_a_winning_holding_is_never_displaced():
    """Evicting winners to chase signals is how a book churns itself flat."""
    s = displacing(displace_margin=1.0)
    opening, displaced = book(s, [1, 0, 0], [0, 1, 1], [3.0, 0.1, 0.1], [0, 0, 0])
    assert not opening.any() and not displaced.any()


def test_a_candidate_inside_the_margin_does_not_displace():
    s = displacing(displace_margin=1.0)
    opening, displaced = book(s, [1, 0, 0], [0, 1, -1], [1.0, 0.5, 0.5], [0, 1, 1])
    assert not opening.any() and not displaced.any()


def test_the_weakest_replaceable_holding_is_the_one_evicted():
    s = displacing(**{"max_positions": 3, "max_weight": 0.3, "displace_margin": 1.0})
    opening, displaced = book(s, [1, 0, 0, 0], [0, 1, 1, -1],
                              [3.0, 1.5, 0.2, 1.4], [0, 1, 1, 1])
    assert displaced[2] and not displaced[1] and not displaced[3]


def test_the_book_never_exceeds_its_cap_through_displacement():
    rng = np.random.default_rng(2)
    s = displacing(**{"max_positions": 3, "max_weight": 0.3, "displace_margin": 0.5})
    for _ in range(200):
        # Start from a book the walk could actually be in: at or under the cap.
        position = np.zeros(8, dtype=int)
        occupied = rng.choice(8, size=rng.integers(0, s.max_positions + 1), replace=False)
        position[occupied] = rng.choice([1, -1], size=len(occupied))
        opening = (position == 0) & (rng.random(8) < 0.5)
        strength = rng.normal(0, 2, 8)
        losing = rng.random(8) < 0.5
        admitted, displaced = book(s, opening, position, strength, losing)
        held_after = int((position != 0).sum()) - int(displaced.sum()) + int(admitted.sum())
        assert held_after <= s.max_positions, "the cap was breached"
        assert not (admitted & (position != 0)).any(), "opened on top of a holding"


def test_a_non_positive_margin_is_refused():
    with pytest.raises(ValueError, match="displace_margin"):
        SRMomentumStrategy(**{**PARAMS, "displace_margin": 0.0})


# ------------------------------------------------- momentum-exhaustion scale-out
def test_exhaustion_reduces_the_position_without_closing_it():
    """A profit rule, not a stop: it takes part off and leaves the rest running."""
    # RETEST[:7] puts the entry on the last prefix bar, so everything after it
    # is the trade's own path — the entry lands at 100.01, not 100.5.
    tail = RETEST[:7] + [101.0, 103.0, 105.0, 105.1, 105.15, 105.2, 105.25]

    def held(**kw):
        w = run(tail, momentum_estimator="ewma", trail_sigmas=8.0, **kw)["target_weight"]
        return w.to_numpy()

    plain, scaled = held(), held(exhaustion_z=3.0, exhaustion_keep=0.5)
    live = plain != 0
    assert (scaled[live] != 0).any(), "the position was closed, not scaled"
    assert (np.abs(scaled[live]) <= np.abs(plain[live]) + 1e-12).all()
    assert (np.abs(scaled[live]) < np.abs(plain[live])).any(), "nothing was taken off"


def test_exhaustion_never_fires_while_the_position_is_losing():
    """Otherwise it is a stop wearing a take-profit's name."""
    tail = RETEST[:7] + [99.8, 99.6, 99.4, 99.2, 99.0, 98.9, 98.8]
    plain = run(tail, momentum_estimator="ewma", trail_sigmas=8.0)["target_weight"].to_numpy()
    scaled = run(tail, momentum_estimator="ewma", trail_sigmas=8.0,
                 exhaustion_z=5.0, exhaustion_keep=0.5)["target_weight"].to_numpy()
    assert np.array_equal(plain, scaled)


def test_exhaustion_scales_a_position_at_most_once():
    """A statistic hovering at the threshold must not bleed the position away."""
    tail = RETEST[:7] + [101.0, 103.0, 105.0] + [105.05] * 12
    w = run(tail, momentum_estimator="ewma", trail_sigmas=8.0,
            exhaustion_z=3.0, exhaustion_keep=0.5)["target_weight"].to_numpy()
    sizes = sorted({round(abs(x), 10) for x in w if x != 0})
    assert len(sizes) <= 2, f"position was reduced more than once: {sizes}"


def test_exhaustion_is_off_by_default():
    baseline = run(RETEST)["target_weight"].to_numpy()
    assert np.array_equal(run(RETEST, exhaustion_z=None)["target_weight"].to_numpy(), baseline)


@pytest.mark.parametrize("kwargs", [{"exhaustion_z": -0.1}, {"exhaustion_keep": 1.0},
                                    {"exhaustion_keep": -0.1}])
def test_invalid_exhaustion_parameters_are_refused(kwargs):
    with pytest.raises(ValueError):
        SRMomentumStrategy(**{**PARAMS, **kwargs})


# ------------------------------------------------------------ stop and reverse
def flip_tail():
    """Long setup, then a decisive move down that should flip it short."""
    return RETEST[:7] + [101.0, 102.0, 103.0] + [101.0, 99.0, 97.0, 95.0, 93.0, 91.0]


def test_a_reversal_can_flip_the_position_instead_of_closing_it():
    kw = dict(momentum_estimator="ewma", trail_sigmas=8.0,
              exit_on_reversal=0.25, reversal_bars=1)
    closing = run(flip_tail(), **kw)["target_weight"].to_numpy()
    flipping = run(flip_tail(), **kw, reverse_on_reversal=True)["target_weight"].to_numpy()

    assert (closing > 0).any() and not (closing < 0).any(), "closing arm took a short"
    assert (flipping < 0).any(), "the flip never opened the other side"


def test_a_flip_never_exceeds_the_weight_an_entry_would_take():
    kw = dict(momentum_estimator="ewma", trail_sigmas=8.0, exit_on_reversal=0.25,
              reversal_bars=1, reverse_on_reversal=True)
    w = run(flip_tail(), **kw)["target_weight"].to_numpy()
    assert np.abs(w).max() <= PARAMS.get("max_weight", 0.15) + 1e-12


def test_a_flip_is_refused_once_the_session_gate_has_closed():
    """A flip is a new position and obeys the same clocks as any entry."""
    kw = dict(momentum_estimator="ewma", trail_sigmas=8.0, exit_on_reversal=0.25,
              reversal_bars=1, reverse_on_reversal=True)
    late = run(flip_tail(), **kw, no_entry_after="09:31")["target_weight"].to_numpy()
    assert not (late < 0).any(), "flipped into a new position after the cutoff"


def test_reverse_without_a_reversal_threshold_is_refused():
    with pytest.raises(ValueError, match="reverse_on_reversal"):
        SRMomentumStrategy(**{**PARAMS, "reverse_on_reversal": True})


# ----------------------------------------------------------------- add back
def add_back_tail():
    """Long setup, a pause that fades momentum, then the move resumes."""
    return RETEST[:7] + [101.0, 103.0, 105.0] + [105.05] * 6 + [107.0, 109.0, 111.0]


def test_a_faded_position_is_topped_back_up_when_the_move_resumes():
    """Driven on real bars: a synthetic path that both fades momentum AND keeps
    breaking fresh levels is fiddly to construct, and the behaviour that matters
    is the one on real data. SPY 2026-03-27 scales out at 12:13 and the move
    then continues for another three hours."""
    lab = pytest.importorskip("qtrader.experiments.session_lab").SessionLab(
        "config/backtest/sr_momentum_index_1min.yaml", "SPY", "2026-03-27"
    )
    session = list(lab.session_index)

    def changes(**kw):
        frame = lab.run(exhaustion_decay=0.5, **kw)
        weight = frame.result.signals.indicators["SPY"]["target_weight"].loc[session]
        return int((weight.diff().abs() > 1e-9).sum())

    assert changes(allow_add_back=True) > changes(), "the size was never restored"


def test_topping_up_never_exceeds_a_full_position():
    w = run(add_back_tail(), momentum_estimator="ewma", trail_sigmas=8.0,
            exhaustion_decay=0.5, require_retest=False,
            allow_add_back=True)["target_weight"].to_numpy()
    assert np.abs(w).max() <= PARAMS.get("max_weight", 0.15) + 1e-12


def test_topping_up_never_flips_the_side():
    w = run(add_back_tail(), momentum_estimator="ewma", trail_sigmas=8.0,
            exhaustion_decay=0.5, require_retest=False,
            allow_add_back=True)["target_weight"].to_numpy()
    live = w[w != 0]
    assert (np.sign(live) == np.sign(live[0])).all(), "the position changed direction"


def test_add_back_is_off_by_default():
    baseline = run(RETEST)["target_weight"].to_numpy()
    assert np.array_equal(
        run(RETEST, allow_add_back=False)["target_weight"].to_numpy(), baseline
    )


def test_the_coarse_source_fires_the_scale_out_later_than_the_decision_grid():
    """The decision grid's statistic peaks at the entry by construction, so a
    decay test on it fires within a handful of bars."""
    lab = pytest.importorskip("qtrader.experiments.session_lab").SessionLab(
        "config/backtest/sr_momentum_index_1min.yaml", "SPY", "2026-03-27"
    )
    session = list(lab.session_index)

    def first_cut(**kw):
        frame = lab.run(exhaustion_decay=0.5, **kw)
        w = frame.result.signals.indicators["SPY"]["target_weight"].loc[session].to_numpy()
        prev = np.concatenate([[0.0], w[:-1]])
        cut = np.flatnonzero(
            (np.abs(w) > 1e-12) & (np.abs(prev) > np.abs(w) + 1e-12)
            & (np.sign(w) == np.sign(prev))
        )
        return int(cut[0]) if len(cut) else len(w)

    assert first_cut(exhaustion_source="coarse") > first_cut()


def test_the_coarse_source_needs_a_second_timeframe():
    context, signals = None, None
    with pytest.raises(ValueError, match="exhaustion_source"):
        SRMomentumStrategy(**{**PARAMS, "exhaustion_source": "hourly"})


# ------------------------------------------------------------- the entry veto


def _veto_frame(index, symbol: str, mask) -> pd.DataFrame:
    return pd.DataFrame({symbol: list(mask)}, index=index)


def test_an_external_veto_suppresses_an_entry_it_covers():
    """The gate is opaque to the strategy: any frame of booleans refuses a
    new position, which is what lets a regime detector be tested as a gate
    without its logic entering this class."""
    opened = run(RETEST)
    fired = opened[opened["candidate"] != 0]
    assert len(fired), "fixture must open a position for the veto to suppress"

    veto = _veto_frame(opened.index, "AAA", [True] * len(opened))
    gated = run(RETEST, entry_veto=veto)
    assert (gated["candidate"] == 0).all()
    assert (gated["target_weight"] == 0).all()


def test_the_veto_refuses_new_risk_but_does_not_close_a_position():
    """Vetoing an open position would be a different experiment — a forced
    flat on a state change, not a refusal to start."""
    opened = run(RETEST)
    entry = opened.index[opened["candidate"] != 0][0]

    # Free before the entry bar, vetoed from the bar after it onwards.
    mask = [timestamp > entry for timestamp in opened.index]
    gated = run(RETEST, entry_veto=_veto_frame(opened.index, "AAA", mask))

    assert (gated["candidate"] != 0).any(), "the entry itself was not vetoed"
    held = gated.loc[gated.index > entry, "target_weight"]
    assert (held != 0).any(), "the veto closed a position instead of refusing one"


def test_a_symbol_the_veto_does_not_mention_is_not_vetoed():
    """Absence of evidence is not a veto: a detector that never covered a
    symbol must leave it trading exactly as before."""
    baseline = run(RETEST)
    other = _veto_frame(baseline.index, "ZZZ", [True] * len(baseline))
    assert_frame_equal(run(RETEST, entry_veto=other), baseline)


def test_a_vetoed_setup_can_be_consumed_instead_of_postponed():
    """Two defensible readings of a veto, and they are not the same trade.

    Postponed, the break stays live and fires once the veto lifts — the same
    move, later. Consumed, price must invalidate the level and break it again.
    Measured on real bars neither dominates (R23 §2), so the switch exists.
    """
    opened = run(RETEST)
    entry = opened.index[opened["candidate"] != 0][0]
    # Vetoed up to and including the bar the baseline entered on.
    mask = [timestamp <= entry for timestamp in opened.index]
    veto = _veto_frame(opened.index, "AAA", mask)

    postponed = run(RETEST, entry_veto=veto, veto_consumes_setup=False)
    consumed = run(RETEST, entry_veto=veto, veto_consumes_setup=True)

    assert (postponed["candidate"] != 0).any(), "the setup should survive the veto"
    assert not (consumed["candidate"] != 0).any(), "a consumed setup must not fire"


# --------------------------------------------------------- per-symbol widths


def test_a_scalar_width_is_unchanged_by_the_per_symbol_machinery():
    """The mapping form must be strictly additive: every existing config keeps
    scalar arithmetic and identical output."""
    assert_frame_equal(run(RETEST, trail_sigmas=2.5),
                       run(RETEST, trail_sigmas={"default": 2.5}))


def test_a_width_mapping_must_name_its_default():
    """Without it, a symbol the fit never saw silently inherits the class
    default, which is not the width anyone chose."""
    with pytest.raises(ValueError, match="default"):
        SRMomentumStrategy(**{**PARAMS, "trail_sigmas": {"AAA": 3.0}})


#: RETEST breaks and runs; this continues it up and then gives part of it back,
#: which is the only shape in which a trailing width can bind at all.
PULLBACK = RETEST + [102.6, 103.2, 103.6, 103.2, 102.7, 102.5, 102.6, 102.8,
                     102.7, 102.9]


def test_a_per_symbol_width_applies_to_the_symbol_it_names():
    """A wider trail on AAA holds the position longer than a narrow one, and
    the width that matters is the one keyed to AAA rather than the default."""
    narrow = run(PULLBACK, stop_sigmas=0.1,
                 trail_sigmas={"AAA": 0.1, "default": 6.0})
    wide = run(PULLBACK, stop_sigmas=0.1,
               trail_sigmas={"AAA": 6.0, "default": 0.1})
    held_narrow = int((narrow["target_weight"] != 0).sum())
    held_wide = int((wide["target_weight"] != 0).sum())
    assert held_narrow == 7, held_narrow
    assert held_wide == 14, held_wide


def test_an_unnamed_symbol_takes_the_mapping_default():
    """AAA is absent from both mappings, so only the default can be acting."""
    assert_frame_equal(
        run(PULLBACK, stop_sigmas=0.1, trail_sigmas={"ZZZ": 6.0, "default": 0.1}),
        run(PULLBACK, stop_sigmas=0.1, trail_sigmas=0.1),
    )


def test_a_mapping_narrower_than_the_stop_is_rejected():
    with pytest.raises(ValueError, match="at least"):
        SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 2.0,
                              "trail_sigmas": {"AAA": 1.0, "default": 3.0}})


def test_every_width_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        SRMomentumStrategy(**{**PARAMS, "trail_sigmas": {"AAA": -1.0, "default": 2.0}})


# ------------------------------------------------------------- the hard stop


def test_the_hard_stop_binds_whatever_volatility_says():
    """R27: a gap-open sigma read 314 bps against 88-106 bps four hours later
    and put the stop beyond the session's high. A cap in bps cannot be inflated
    by the estimate it bounds."""
    loose = SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 2.0, "trail_sigmas": 3.5})
    capped = SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 2.0, "trail_sigmas": 3.5,
                                   "hard_stop_bps": 150.0})
    sigma = np.array([[0.0314]])          # 314 bps, the AMD reading
    atr, price = np.array([[5.0]]), np.array([[512.0]])
    assert loose._initial_stop_fraction(sigma, atr, price)[0, 0] == pytest.approx(0.0628)
    assert capped._initial_stop_fraction(sigma, atr, price)[0, 0] == pytest.approx(0.0150)


def test_the_hard_stop_never_widens_a_stop_that_is_already_tighter():
    """It is a ceiling, not a target: a quiet bar must keep its narrow stop."""
    capped = SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 2.0, "trail_sigmas": 3.5,
                                   "hard_stop_bps": 150.0})
    quiet = capped._initial_stop_fraction(
        np.array([[0.0020]]), np.array([[1.0]]), np.array([[512.0]]))
    assert quiet[0, 0] == pytest.approx(0.0040)


def test_the_atr_cap_and_the_hard_stop_compose_to_the_tighter_of_the_two():
    strategy = SRMomentumStrategy(**{**PARAMS, "stop_sigmas": 2.0, "trail_sigmas": 3.5,
                                     "initial_stop_atr": 2.0,
                                     "hard_stop_bps": 150.0})
    # 2 ATR = 2 * 3.0 / 512 = 117 bps, tighter than the 150 bps ceiling.
    distance = strategy._initial_stop_fraction(
        np.array([[0.0314]]), np.array([[3.0]]), np.array([[512.0]]))
    assert distance[0, 0] == pytest.approx(2 * 3.0 / 512.0)


def test_a_non_positive_hard_stop_is_rejected():
    with pytest.raises(ValueError, match="hard_stop_bps"):
        SRMomentumStrategy(**{**PARAMS, "hard_stop_bps": 0.0})


# ------------------------------------------- reading the tape while holding


def test_breaks_stop_forming_once_a_position_is_open_by_default():
    """The behaviour R27 diagnosed: a held symbol goes blind."""
    frame = run(PULLBACK)
    entry = frame.index[frame["candidate"] != 0][0]
    after = frame.loc[frame.index > entry]
    assert after["watched_side"].fillna(0).eq(0).all()


def test_tracking_while_held_keeps_the_level_machine_running():
    frame = run(PULLBACK, track_breaks_while_held=True)
    entry = frame.index[frame["candidate"] != 0][0]
    after = frame.loc[frame.index > entry]
    assert after["watched_side"].fillna(0).ne(0).any(), (
        "no break registered under the open position"
    )
