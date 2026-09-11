"""Own-path index momentum: leakage, single-name scores, no peer leakage."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.score import combine_score
from qtrader.strategies.index_momentum import FACTORS, IndexMomentumStrategy
from tests.conftest import make_context

WARMUP = 80
PARAMS = dict(
    min_factors=1,
    min_rvol=None,
    min_er=None,
    no_entry_before=None,
    no_entry_after=None,
    flat_time=None,
    max_positions=2,
    max_weight=0.5,
    vol_window=15,
    atr_window=15,
    max_holding_bars=None,
    min_holding_bars=0,
    reentry_cooldown_bars=0,
)


def _paths(n: int) -> dict[str, list[float]]:
    rng = np.random.default_rng(0)
    noise = lambda: list(400 + np.cumsum(rng.normal(0, 0.02, n)))
    return {
        "QQQ": list(100 + np.arange(n) * 0.05),
        "SPY": noise(),
    }


def build(n: int = WARMUP, **overrides):
    context = make_context(_paths(n), symbols=["QQQ", "SPY"])
    strategy = IndexMomentumStrategy(**{**PARAMS, **overrides})
    return strategy, context, strategy.generate(context)


def test_the_score_on_a_prefix_matches_the_score_on_the_whole_history():
    n = WARMUP
    full = build(n)[2].scores
    cut = 50
    prefix = IndexMomentumStrategy(**PARAMS).generate(
        make_context({k: v[:cut] for k, v in _paths(n).items()}, symbols=["QQQ", "SPY"])
    ).scores
    pd.testing.assert_frame_equal(full.iloc[:cut], prefix, check_freq=False)


def test_rewriting_the_future_does_not_move_a_single_past_score():
    n = WARMUP
    paths = _paths(n)
    cut = 50
    original = IndexMomentumStrategy(**PARAMS).generate(
        make_context(paths, symbols=["QQQ", "SPY"])
    ).scores
    tampered = {k: v[:cut] + [x * 1.35 for x in v[cut:]] for k, v in paths.items()}
    after = IndexMomentumStrategy(**PARAMS).generate(
        make_context(tampered, symbols=["QQQ", "SPY"])
    ).scores
    pd.testing.assert_frame_equal(
        original.iloc[:cut], after.iloc[:cut], check_freq=False
    )


def test_a_single_symbol_still_has_a_finite_score():
    """The reason this strategy exists: a cross-sectional z is NaN at N=1."""
    n = WARMUP
    context = make_context(_paths(n), symbols=["QQQ"])
    signals = IndexMomentumStrategy(**PARAMS).generate(context)
    session_tail = signals.scores["QQQ"].iloc[20:]
    assert session_tail.notna().any()
    assert np.isfinite(session_tail.dropna()).all()


def test_moving_the_other_index_does_not_change_this_ones_score():
    n = WARMUP
    paths = _paths(n)
    original = IndexMomentumStrategy(**PARAMS).generate(
        make_context(paths, symbols=["QQQ", "SPY"])
    ).scores["SPY"]
    paths["QQQ"] = [x * 1.2 for x in paths["QQQ"]]
    after = IndexMomentumStrategy(**PARAMS).generate(
        make_context(paths, symbols=["QQQ", "SPY"])
    ).scores["SPY"]
    pd.testing.assert_series_equal(original, after, check_names=False)


def test_combine_score_skips_nans_and_renormalises():
    index = pd.RangeIndex(3)
    a = pd.DataFrame({"X": [1.0, np.nan, 3.0]}, index=index)
    b = pd.DataFrame({"X": [1.0, 2.0, np.nan]}, index=index)
    out = combine_score({"a": a, "b": b}, {"a": 1.0, "b": 1.0}, min_factors=1)
    assert out.loc[0, "X"] == pytest.approx(1.0)
    assert out.loc[1, "X"] == pytest.approx(2.0)
    assert out.loc[2, "X"] == pytest.approx(3.0)


def test_combine_score_is_nan_until_enough_factors_are_present():
    index = pd.RangeIndex(1)
    a = pd.DataFrame({"X": [1.0]}, index=index)
    b = pd.DataFrame({"X": [np.nan]}, index=index)
    out = combine_score({"a": a, "b": b}, {"a": 1.0, "b": 1.0}, min_factors=2)
    assert pd.isna(out.loc[0, "X"])


def test_the_six_directional_factors_are_published():
    signals = build()[2]
    published = signals.indicators["QQQ"].columns
    missing = [name for name in FACTORS if name not in published]
    assert missing == []
    assert "rvol" in published and "er_15" in published
    assert "score_rank" not in published


def test_a_clean_rally_scores_positive_once_the_windows_are_warm():
    signals = build()[2]
    tail = signals.scores["QQQ"].iloc[40:]
    assert tail.notna().all()
    assert float(tail.mean()) > 0.5


def test_rvol_cannot_carry_a_weight():
    with pytest.raises(ValueError, match="confirmation gates"):
        IndexMomentumStrategy(**{**PARAMS, "weights": {"session_z": 1.0, "rvol": 1.0}})


def test_vwap_z_is_on_the_same_scale_as_session_z():
    """Without sqrt(n) in the VWAP denominator the term silently dominates."""
    rng = np.random.default_rng(1)
    n = 200
    paths = {
        "QQQ": list(100 + np.cumsum(rng.normal(0, 0.15, n))),
        "SPY": list(400 + np.cumsum(rng.normal(0, 0.15, n))),
    }
    signals = IndexMomentumStrategy(**PARAMS).generate(
        make_context(paths, symbols=["QQQ", "SPY"])
    )
    frame = signals.indicators["QQQ"].iloc[40:]
    session_std = float(frame["session_z"].std())
    vwap_std = float(frame["vwap_z"].std())
    assert session_std > 0 and vwap_std > 0
    assert vwap_std / session_std < 3.0
    assert session_std / vwap_std < 3.0


def test_the_weights_on_a_prefix_match_the_weights_on_the_whole_history():
    n = WARMUP
    full = build(n)[2].target_weights
    cut = 50
    prefix = IndexMomentumStrategy(**PARAMS).generate(
        make_context({k: v[:cut] for k, v in _paths(n).items()}, symbols=["QQQ", "SPY"])
    ).target_weights
    pd.testing.assert_frame_equal(full.iloc[:cut], prefix, check_freq=False)


def _entries(weights: pd.Series) -> int:
    w = np.asarray(weights, dtype=float)
    prev = np.concatenate([[0.0], w[:-1]])
    return int(((w != 0) & (prev == 0)).sum())


def test_a_time_stop_slices_a_trend_that_should_be_one_hold():
    once = _entries(build(n=80, max_holding_bars=None)[2].target_weights["QQQ"])
    sliced = _entries(build(n=80, max_holding_bars=15)[2].target_weights["QQQ"])
    assert once == 1
    assert sliced > 1


def test_reentry_cooldown_blocks_the_bars_after_a_time_stop():
    """A still-trending score would re-enter the next bar; the cooldown is why it does not."""
    weights = build(
        n=80, max_holding_bars=10, reentry_cooldown_bars=8,
    )[2].target_weights["QQQ"]
    arr = weights.to_numpy()
    for i in range(1, len(arr)):
        if arr[i - 1] != 0 and arr[i] == 0:
            assert (arr[i:i + 8] == 0).all()
            return
    pytest.fail("expected the time stop to flatten at least once")
