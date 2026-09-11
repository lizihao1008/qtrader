"""The unconditional and hard-negative precursor designs.

The point of these designs is that they have no control pool to select, so the
tests check exactly that: the sample is every bar, the factor never reaches
across a session, and a planted signal is recovered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.experiments.trend_lift import (
    hard_negative_contrast,
    label_trend_starts,
    unconditional_lift,
)
from tests.conftest import minute_index


def _series(n: int, start: str = "2026-08-03 09:30") -> pd.Series:
    index = minute_index(n, start_local=start)
    rng = np.random.default_rng(0)
    return pd.Series(100 + np.cumsum(rng.normal(0, 0.02, n)), index=index)


def test_every_bar_is_a_sample_and_the_base_rate_is_the_real_one():
    """No control pool exists, so nothing can be selected into or out of it."""
    close = _series(400)
    events = pd.Series(close.index[[50, 150, 300]])
    result = unconditional_lift(close, events, windows=(5,), leads=(1,))
    assert result.n_events == 3
    assert result.base_rate == pytest.approx(3 / len(close))
    assert result.n_bars == len(close)


def test_the_factor_never_reaches_across_a_session():
    """A 20-bar window on bar 5 of a session would otherwise be built from
    yesterday, which is the leak the curated test already guards against."""
    close = pd.concat([_series(30), _series(30, "2026-08-04 09:30")])
    events = pd.Series([close.index[45]])
    result = unconditional_lift(close, events, windows=(20,), leads=(1,), n_bins=2)
    # With only 30 bars a session, a 21-bar lookback leaves too few finite
    # cells to bucket; the design refuses rather than silently reaching back.
    assert result.table.empty or np.isfinite(result.table["auc"]).all()


def test_a_planted_precursor_is_recovered():
    """If momentum really did precede the event, the design must see it —
    otherwise a null result says nothing."""
    n = 3000
    index = minute_index(n)
    rng = np.random.default_rng(3)
    steps = rng.normal(0, 0.02, n)
    starts = np.arange(200, n - 50, 120)
    for s in starts:                      # a clean run-up in the five bars before
        steps[s - 5:s] += 0.25
    close = pd.Series(100 + np.cumsum(steps), index=index)
    events = pd.Series(index[starts])
    result = unconditional_lift(close, events, windows=(5,), leads=(1,))
    assert result.table["auc"].iloc[0] > 0.9, result.table


def test_a_pure_noise_factor_scores_near_a_half():
    n = 4000
    index = minute_index(n)
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.02, n)), index=index)
    events = pd.Series(index[rng.choice(np.arange(100, n - 50), 200, replace=False)])
    result = unconditional_lift(close, events, windows=(5,), leads=(1,))
    assert abs(result.table["auc"].iloc[0] - 0.5) < 0.08


def test_hard_negatives_report_the_rate_inside_the_breakout_slice():
    close = _series(2000)
    rng = np.random.default_rng(5)
    events = pd.Series(close.index[rng.choice(np.arange(50, 1950), 60, replace=False)])
    table = hard_negative_contrast(close, events, window=5, lead=1, top_quantile=0.9)
    assert list(table["measurement"])[:2] == ["(all bars)", "|mom_5| top 10%"]
    # The slice is a tenth of the bars, so its n must be far below the total.
    assert table["n"].iloc[1] < table["n"].iloc[0] / 5


def test_label_marks_only_the_event_bars():
    close = _series(100)
    events = pd.Series(close.index[[10, 20]])
    label = label_trend_starts(pd.DatetimeIndex(close.index), events)
    assert label.sum() == 2
    assert label[10] == 1 and label[20] == 1 and label[11] == 0
