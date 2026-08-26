"""Rank IC diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.backtest.signal_metrics import forward_return, rank_ic
from tests.conftest import minute_index


def test_forward_return_looks_ahead_and_stops_at_the_session_end():
    index = minute_index(4)
    close = pd.DataFrame({"AAA": [100.0, 110.0, 121.0, 133.1]}, index=index)

    ahead = forward_return(close, 1)["AAA"]
    assert ahead.iloc[0] == pytest.approx(np.log(110 / 100))
    assert pd.isna(ahead.iloc[-1])  # nothing left in the session to look at


def test_perfect_ordering_gives_an_ic_of_one():
    index = minute_index(1)
    symbols = ["A", "B", "C", "D", "E"]
    scores = pd.DataFrame([[1.0, 2.0, 3.0, 4.0, 5.0]], index=index, columns=symbols)
    forward = pd.DataFrame([[0.1, 0.2, 0.3, 0.4, 0.5]], index=index, columns=symbols)
    eligible = pd.DataFrame(True, index=index, columns=symbols)

    assert rank_ic(scores, forward, eligible).iloc[0] == pytest.approx(1.0)
    assert rank_ic(-scores, forward, eligible).iloc[0] == pytest.approx(-1.0)


def test_thin_cross_sections_are_dropped_rather_than_reported():
    index = minute_index(1)
    symbols = ["A", "B"]
    scores = pd.DataFrame([[1.0, 2.0]], index=index, columns=symbols)
    forward = pd.DataFrame([[0.1, 0.2]], index=index, columns=symbols)
    eligible = pd.DataFrame(True, index=index, columns=symbols)

    assert pd.isna(rank_ic(scores, forward, eligible, min_names=5).iloc[0])
