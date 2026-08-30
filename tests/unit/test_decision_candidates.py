"""Finding position runs, and removing them without touching anything else."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.decision.candidates import apply_vetoes, find_candidates
from tests.conftest import minute_index


def weights(**columns) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=minute_index(n), dtype=float)


def test_a_candidate_is_the_bar_a_position_opens():
    w = weights(A=[0, 0.1, 0.1, 0.1, 0, 0])
    (candidate,) = find_candidates(w)

    assert (candidate.symbol, candidate.direction, candidate.position) == ("A", 1, 1)
    assert candidate.end_position == 3 and candidate.hold_bars == 3


def test_holding_through_a_size_change_is_still_one_position():
    """Only the sign starts a run; a rebalance inside a holding does not."""
    w = weights(A=[0, 0.1, 0.2, 0.05, 0, 0])
    (candidate,) = find_candidates(w)
    assert candidate.hold_bars == 3


def test_a_reversal_is_a_new_candidate_not_a_continuation():
    w = weights(A=[0, 0.1, 0.1, -0.1, -0.1, 0])
    first, second = find_candidates(w)

    assert (first.direction, first.position, first.end_position) == (1, 1, 2)
    assert (second.direction, second.position, second.end_position) == (-1, 3, 4)


def test_a_position_open_at_the_last_bar_still_ends():
    w = weights(A=[0, 0, 0.1, 0.1])
    (candidate,) = find_candidates(w)
    assert candidate.end_position == 3


def test_candidates_come_back_in_time_order_across_symbols():
    w = weights(A=[0, 0.1, 0.1, 0, 0], B=[0, 0, 0.2, 0.2, 0], C=[0.3, 0.3, 0, 0, 0])
    order = [(c.position, c.symbol) for c in find_candidates(w)]
    assert order == [(0, "C"), (1, "A"), (2, "B")]


def test_a_frame_with_no_positions_yields_nothing():
    assert find_candidates(weights(A=[0, 0, 0], B=[0, 0, 0])) == []


# ------------------------------------------------------------------- vetoing
def test_a_veto_zeroes_the_whole_holding_not_just_the_entry():
    """Zeroing only the entry bar would open the position one bar later."""
    w = weights(A=[0, 0.1, 0.1, 0.1, 0, 0])
    narrowed = apply_vetoes(w, find_candidates(w))
    assert narrowed["A"].tolist() == [0, 0, 0, 0, 0, 0]


def test_a_veto_leaves_every_other_position_untouched():
    w = weights(A=[0, 0.1, 0.1, 0, 0], B=[0, 0.2, 0.2, 0.2, 0])
    target = next(c for c in find_candidates(w) if c.symbol == "A")

    narrowed = apply_vetoes(w, [target])
    assert narrowed["A"].tolist() == [0, 0, 0, 0, 0]
    pd.testing.assert_series_equal(narrowed["B"], w["B"])


def test_vetoing_only_ever_removes_exposure():
    """The layer must not be able to open, enlarge or flip anything."""
    rng = np.random.default_rng(0)
    raw = rng.choice([0.0, 0.1, -0.1, 0.2], size=(40, 3))
    w = pd.DataFrame(raw, index=minute_index(40), columns=["A", "B", "C"])
    candidates = find_candidates(w)

    narrowed = apply_vetoes(w, candidates[::2])
    kept = narrowed.to_numpy() != 0
    assert np.array_equal(narrowed.to_numpy()[kept], w.to_numpy()[kept]), "a weight changed"
    assert (narrowed.abs().sum(axis=1) <= w.abs().sum(axis=1) + 1e-12).all()


def test_vetoing_nothing_returns_the_frame_unchanged():
    w = weights(A=[0, 0.1, 0.1, 0])
    assert apply_vetoes(w, []) is w
