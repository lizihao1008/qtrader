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

from qtrader.strategies.sr_momentum import LONG, SRMomentumStrategy
from tests.conftest import make_context

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


def test_a_blocked_setup_re_enters_when_the_gate_opens():
    """The blackout delays a live setup, it does not cancel it.

    Price is still beyond the level, so the break re-registers every bar and the
    entry lands on the first admitted bar. That matters for reading a backtest
    of this parameter: some trades move rather than disappear.
    """
    assert entry_times(run(RETEST, no_entry_before="11:00")) == ["11:00"]


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
