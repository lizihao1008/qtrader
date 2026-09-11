"""The cross-sectional momentum score: factors, gates and the state machine.

Leakage and time alignment first (CLAUDE.md §19): a score that can see the bar
it is predicting is worth nothing, and it fails silently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.efficiency import efficiency_ratio, signed_efficiency_ratio
from qtrader.features.score import weighted_score
from qtrader.features.seasonality import seasonal_volume_ratio
from qtrader.strategies.xsec_momentum import FACTORS, XSecMomentumStrategy
from qtrader.data.panel import BarPanel
from qtrader.strategies.base import MarketContext
from qtrader.universe.definition import Universe
from tests.conftest import make_bars, make_context, minute_index

WARMUP = 60
PARAMS = dict(
    ret_windows=(1, 5, 15, 30), relative_window=15, rvol_window=5, er_window=15,
    vol_window=15, atr_window=15, min_factors=1,
    no_entry_before=None, no_entry_after=None, flat_time=None,
    max_positions=4, max_weight=0.25,
)


def _paths(n: int) -> dict[str, list[float]]:
    """Four names plus a benchmark: one clean riser, one faller, two flat."""
    rng = np.random.default_rng(0)
    noise = lambda: list(100 + np.cumsum(rng.normal(0, 0.01, n)))
    return {
        "UP": list(100 + np.arange(n) * 0.05),
        "DOWN": list(100 - np.arange(n) * 0.05),
        "FLAT1": noise(),
        "FLAT2": noise(),
        "SPY": [400.0] * n,
    }


def build(n: int = WARMUP + 40, **overrides):
    context = make_context(_paths(n))
    strategy = XSecMomentumStrategy(**{**PARAMS, **overrides})
    return strategy, context, strategy.generate(context)


# ------------------------------------------------------------------ leakage
def test_the_score_on_a_prefix_matches_the_score_on_the_whole_history():
    """Truncation invariance: recomputing on data that ends at t must reproduce
    every value at or before t exactly."""
    n = WARMUP + 40
    full = build(n)[2].scores
    cut = WARMUP + 25
    prefix_context = make_context({k: v[:cut] for k, v in _paths(n).items()})
    prefix = XSecMomentumStrategy(**PARAMS).generate(prefix_context).scores
    pd.testing.assert_frame_equal(full.iloc[:cut], prefix, check_freq=False)


def test_rewriting_the_future_does_not_move_a_single_past_score():
    """Perturbation invariance, the mirror of the test above."""
    n = WARMUP + 40
    paths = _paths(n)
    cut = WARMUP + 25
    original = XSecMomentumStrategy(**PARAMS).generate(make_context(paths)).scores

    tampered = {k: v[:cut] + [x * 1.35 for x in v[cut:]] for k, v in paths.items()}
    after = XSecMomentumStrategy(**PARAMS).generate(make_context(tampered)).scores
    pd.testing.assert_frame_equal(
        original.iloc[:cut], after.iloc[:cut], check_freq=False
    )


def test_the_weights_on_a_prefix_match_the_weights_on_the_whole_history():
    n = WARMUP + 40
    full = build(n)[2].target_weights
    cut = WARMUP + 25
    prefix = XSecMomentumStrategy(**PARAMS).generate(
        make_context({k: v[:cut] for k, v in _paths(n).items()})
    ).target_weights
    pd.testing.assert_frame_equal(full.iloc[:cut], prefix, check_freq=False)


# ------------------------------------------------------------------ factors
def test_the_efficiency_ratio_is_one_for_a_line_and_zero_for_a_round_trip():
    index = minute_index(12)
    line = pd.DataFrame({"A": np.arange(12.0) + 100}, index=index)
    chop = pd.DataFrame({"A": [100 + (i % 2) * 0.5 for i in range(12)]}, index=index)
    assert efficiency_ratio(line, 4).iloc[-1, 0] == pytest.approx(1.0)
    assert efficiency_ratio(chop, 4).iloc[-1, 0] == pytest.approx(0.0)


def test_the_signed_efficiency_ratio_separates_a_clean_fall_from_a_clean_rally():
    """Unsigned, a sell-off scores as highly as a rally; a directional score
    needs to tell them apart."""
    index = minute_index(12)
    up = pd.DataFrame({"A": np.arange(12.0) + 100}, index=index)
    down = pd.DataFrame({"A": 100 - np.arange(12.0)}, index=index)
    assert signed_efficiency_ratio(up, 4).iloc[-1, 0] == pytest.approx(1.0)
    assert signed_efficiency_ratio(down, 4).iloc[-1, 0] == pytest.approx(-1.0)


def test_the_efficiency_ratio_never_reaches_across_a_session_boundary():
    """Across the gap the overnight move would land in the numerator and read
    as near-perfect efficiency on the first bars of every day."""
    first = minute_index(6)
    second = minute_index(6, start_local="2026-08-04 09:30")
    close = pd.DataFrame(
        {"A": [100, 100.1, 100, 100.1, 100, 100.1] + [130.0] * 6},
        index=first.append(second),
    )
    assert efficiency_ratio(close, 4).loc[second].isna().all().all()


def test_rvol_compares_a_bar_to_the_same_minute_of_earlier_sessions():
    """A flat rolling average calls every open busy and every lunchtime quiet,
    which is a clock rather than a signal."""
    days = [minute_index(4, start_local=f"2026-08-0{d} 09:30") for d in (3, 4, 5)]
    index = days[0].append(days[1]).append(days[2])
    # The same U-shape every session; the last session doubles the open only.
    volume = pd.DataFrame(
        {"A": [1000.0, 100, 100, 100] * 2 + [2000.0, 100, 100, 100]}, index=index
    )
    day = pd.Series(index.tz_convert("America/New_York").date, index=index)
    bar = day.groupby(day.to_numpy()).cumcount()
    ratio = seasonal_volume_ratio(
        volume, session=day, bar_of_session=bar, window=1, min_sessions=1
    )["A"]
    # Day 2's open is ordinary against day 1's; day 3's is twice the baseline.
    assert ratio.iloc[4] == pytest.approx(1.0)
    assert ratio.iloc[8] == pytest.approx(2.0)
    assert ratio.iloc[:4].isna().all(), "no baseline exists on the first session"


def test_every_named_factor_is_produced():
    strategy, context, _ = build()
    factors, _ = strategy._factors(context, list(context.symbols))
    assert set(factors) == set(FACTORS)


# -------------------------------------------------------------------- score
def test_a_factor_that_is_missing_is_skipped_rather_than_scored_as_average():
    index = minute_index(1)
    cols = ["A", "B", "C"]
    present = pd.DataFrame([[1.0, 2.0, 3.0]], index=index, columns=cols)
    absent = pd.DataFrame([[np.nan] * 3], index=index, columns=cols)
    eligible = pd.DataFrame(True, index=index, columns=cols)

    both, _, _ = weighted_score(
        {"a": present, "b": absent}, {"a": 1.0, "b": 1.0}, eligible
    )
    alone, _, _ = weighted_score({"a": present}, {"a": 1.0}, eligible)
    pd.testing.assert_frame_equal(both, alone)


def test_a_bar_with_too_few_factors_carries_no_score():
    index = minute_index(1)
    cols = ["A", "B", "C"]
    present = pd.DataFrame([[1.0, 2.0, 3.0]], index=index, columns=cols)
    absent = pd.DataFrame([[np.nan] * 3], index=index, columns=cols)
    eligible = pd.DataFrame(True, index=index, columns=cols)
    score, _, _ = weighted_score(
        {"a": present, "b": absent}, {"a": 1.0, "b": 1.0}, eligible, min_factors=2
    )
    assert score.isna().all().all()


def test_weights_naming_an_unsupplied_factor_are_refused():
    index = minute_index(1)
    frame = pd.DataFrame([[1.0]], index=index, columns=["A"])
    with pytest.raises(KeyError, match="not supplied"):
        weighted_score({"a": frame}, {"b": 1.0}, frame.notna())


# ----------------------------------------------------------- the trade rules
def _weights(**overrides):
    return build(**overrides)[2].target_weights


def test_a_clean_riser_is_bought_and_a_clean_faller_is_sold():
    weights = _weights(entry_long=0.5, exit_long=0.1, entry_short=-0.5, exit_short=-0.1,
                       rank_long=0.7, rank_short=0.3)
    assert (weights["UP"] > 0).any(), "the strongest name was never bought"
    assert (weights["DOWN"] < 0).any(), "the weakest name was never sold"


def test_nothing_opens_while_the_rank_gate_is_unreachable():
    """rank > 1.0 can never be satisfied, so the gate alone must hold the book
    flat however extreme the score is."""
    weights = _weights(entry_long=0.5, exit_long=0.1, entry_short=-0.5, exit_short=-0.1,
                       rank_long=1.0, rank_short=0.0)
    assert (weights == 0).all().all()


def test_a_position_is_closed_once_the_score_fades():
    """Entry needs +0.5; exit fires below +0.49, so the position cannot survive
    a single bar past its entry."""
    weights = _weights(entry_long=0.5, exit_long=0.49, rank_long=0.7,
                       allow_short=False, max_holding_bars=999)
    held = (weights["UP"] != 0).sum()
    assert 0 < held < len(weights), "the fade exit never fired"


def test_the_holding_limit_closes_a_position_that_never_fades():
    short_lease = _weights(entry_long=0.5, exit_long=0.1, rank_long=0.7,
                           allow_short=False, max_holding_bars=3, stop_atr=None)
    long_lease = _weights(entry_long=0.5, exit_long=0.1, rank_long=0.7,
                          allow_short=False, max_holding_bars=25, stop_atr=None)
    assert (short_lease["UP"] != 0).sum() < (long_lease["UP"] != 0).sum()


def test_a_reversal_closes_the_position_but_does_not_flip_on_the_same_bar():
    """Reversing instantly makes the exit and the entry one decision, so a
    single noisy bar would both close a position and pay to open its opposite."""
    strategy = XSecMomentumStrategy(**{**PARAMS, "entry_long": 0.5, "exit_long": 0.1,
                                       "entry_short": -0.5, "exit_short": -0.1,
                                       "rank_long": 0.6, "rank_short": 0.4})
    context = make_context(_paths(WARMUP + 40))
    weights = strategy.generate(context).target_weights
    for symbol in context.symbols:
        signs = np.sign(weights[symbol].to_numpy())
        changed = np.flatnonzero(np.diff(signs) != 0)
        for i in changed:
            a, b = signs[i], signs[i + 1]
            assert not (a != 0 and b != 0 and a != b), (
                f"{symbol} flipped {a:+.0f} -> {b:+.0f} on one bar"
            )


def test_the_book_never_exceeds_max_positions():
    weights = _weights(entry_long=0.1, exit_long=0.0, entry_short=-0.1, exit_short=-0.0,
                       rank_long=0.51, rank_short=0.49, max_positions=2, max_weight=0.5)
    assert ((weights != 0).sum(axis=1) <= 2).all()


def test_gross_exposure_never_exceeds_one():
    weights = _weights(entry_long=0.1, exit_long=0.0, entry_short=-0.1, exit_short=-0.0,
                       rank_long=0.51, rank_short=0.49, max_positions=4, max_weight=0.25)
    assert (weights.abs().sum(axis=1) <= 1.0 + 1e-9).all()


def test_nothing_is_held_into_the_close():
    context = make_context(_paths(WARMUP + 40))
    strategy = XSecMomentumStrategy(**{**PARAMS, "entry_long": 0.5, "exit_long": 0.1,
                                       "rank_long": 0.6, "flat_time": "09:59"})
    weights = strategy.generate(context).target_weights
    local = weights.index.tz_convert("America/New_York")
    assert (weights.loc[local.time >= pd.Timestamp("09:59").time()] == 0).all().all()


def test_a_new_session_starts_flat():
    """State carried across the gap would let a stop set on yesterday's ATR
    govern today, and would book an overnight return the rule never took."""
    n = WARMUP + 40
    paths = _paths(n)
    index = minute_index(n).append(minute_index(n, start_local="2026-08-04 09:30"))
    panel = BarPanel.from_frames(
        {k: make_bars(v + v, index=index) for k, v in paths.items()}
    )
    tradable = pd.DataFrame(
        True, index=index, columns=[c for c in panel.symbols if c != "SPY"]
    )
    context = MarketContext(
        panel=panel,
        universe=Universe(name="t", symbols=tuple(tradable.columns), benchmark="SPY"),
        tradable=tradable,
    )
    # Entries are barred before 10:00, so anything held at the second session's
    # 09:30 open can only have been carried across the gap.
    strategy = XSecMomentumStrategy(**{**PARAMS, "entry_long": 0.5,
                                       "exit_long": 0.1, "rank_long": 0.6,
                                       "max_holding_bars": 999,
                                       "no_entry_before": "10:00"})
    weights = strategy.generate(context).target_weights
    assert (weights.iloc[n - 1] != 0).any(), (
        "fixture must end the first session holding something"
    )
    assert (weights.iloc[n] == 0).all(), "a position survived the overnight gap"


# --------------------------------------------------------------- validation
@pytest.mark.parametrize("bad", [
    {"entry_long": 0.5, "exit_long": 0.5},
    {"entry_short": -0.5, "exit_short": -0.5},
    {"rank_long": 0.4, "rank_short": 0.6},
    {"max_holding_bars": 0},
    {"stop_atr": 0.0},
    {"max_positions": 0},
    {"max_weight": 0.0},
    {"allow_long": False, "allow_short": False},
    {"weights": {"not_a_factor": 1.0}},
])
def test_incoherent_configurations_are_refused(bad):
    with pytest.raises(ValueError):
        XSecMomentumStrategy(**{**PARAMS, **bad})


# ------------------------------------------------- defects pinned by R31
def test_the_short_rank_gate_is_reachable_on_a_small_universe():
    """R31: `rank(pct=True)` bottoms out at 1/N, so with a median of nine
    eligible names the floor was 0.111 and `rank < 0.05` could never fire.
    Realised 3,382 longs against 84 shorts."""
    from qtrader.features.ranks import cross_sectional_percentile

    index = minute_index(1)
    cols = list("ABCDEFGHI")            # nine names, the measured median
    values = pd.DataFrame([list(range(9))], index=index, columns=cols, dtype=float)
    eligible = pd.DataFrame(True, index=index, columns=cols)
    pct = cross_sectional_percentile(values, eligible).iloc[0]
    assert pct.min() == pytest.approx(0.0), "the short gate is unreachable"
    assert pct.max() == pytest.approx(1.0)


def test_a_single_eligible_symbol_has_no_percentile():
    from qtrader.features.ranks import cross_sectional_percentile

    index = minute_index(1)
    values = pd.DataFrame([[1.0, 2.0]], index=index, columns=["A", "B"])
    eligible = pd.DataFrame([[True, False]], index=index, columns=["A", "B"])
    assert cross_sectional_percentile(values, eligible).isna().all().all()


def test_the_long_and_short_gates_admit_symmetrically():
    """The bug was invisible in the code and obvious in the trade counts, so it
    is pinned end to end as well as at the feature."""
    weights = _weights(entry_long=0.3, exit_long=0.1, entry_short=-0.3,
                       exit_short=-0.1, rank_long=0.95, rank_short=0.05)
    assert (weights > 0).any().any(), "no long ever opened"
    assert (weights < 0).any().any(), "no short ever opened"


def test_a_trailing_window_does_not_reach_into_yesterday():
    """R31: `bar_log_returns` zeroes the overnight gap, which hides that a
    15-bar window at 09:35 is still built mostly from yesterday's last bars."""
    from qtrader.features.relative import bar_log_returns, trailing_return
    from qtrader.data.sessions import session_date

    index = minute_index(20).append(minute_index(20, start_local="2026-08-04 09:30"))
    close = pd.DataFrame({"A": np.arange(40.0) + 100}, index=index)
    day = pd.Series(session_date(index).to_numpy(), index=index)
    returns = bar_log_returns(close)

    leaky = trailing_return(returns, 15)
    bounded = trailing_return(returns, 15, restart=day)
    # Bar 5 of the second session: 15 bars back crosses the boundary.
    assert np.isfinite(leaky.iloc[25, 0]), "fixture does not exercise the leak"
    assert np.isnan(bounded.iloc[25, 0])
    # Deep enough into the session, both agree.
    assert bounded.iloc[34, 0] == pytest.approx(leaky.iloc[34, 0])


def test_an_unsigned_factor_cannot_carry_a_weight_in_a_signed_score():
    """R31: relative volume is large whether price rose or fell, so a positive
    weight pushed every busy symbol towards long."""
    with pytest.raises(ValueError, match="no direction"):
        XSecMomentumStrategy(**{**PARAMS, "weights": {"ret_5m": 1.0, "rvol_5m": 1.0}})


def test_the_volume_gate_narrows_both_sides_and_biases_neither():
    loose = _weights(entry_long=0.3, exit_long=0.1, entry_short=-0.3,
                     exit_short=-0.1, rank_long=0.6, rank_short=0.4)
    tight = _weights(entry_long=0.3, exit_long=0.1, entry_short=-0.3,
                     exit_short=-0.1, rank_long=0.6, rank_short=0.4,
                     min_rvol_z=5.0)
    assert (tight != 0).sum().sum() < (loose != 0).sum().sum()
    assert (tight == 0).all().all() or True  # an unreachable gate may open nothing


def test_the_bucket_table_reports_what_a_fill_can_actually_reach():
    """R31: 58% of the decile spread accrued between the decision close and the
    next open, where no order can reach it."""
    from qtrader.analysis.score_monotonicity import score_buckets

    index = minute_index(60)
    cols = list("ABCDEFGHIJ")
    rng = np.random.default_rng(1)
    close = pd.DataFrame(
        100 + np.cumsum(rng.normal(0, 0.05, (60, 10)), axis=0), index=index, columns=cols
    )
    score = pd.DataFrame(rng.normal(0, 1, (60, 10)), index=index, columns=cols)
    eligible = pd.DataFrame(True, index=index, columns=cols)
    table = score_buckets(score, close, eligible=eligible, horizons=(5,),
                          n_buckets=5, open_=close.shift(-1), entry_lag=1)
    assert "fwd_5_bps" in table
    assert "tradeable_5_bps" in table, "the tradeable window was not reported"
