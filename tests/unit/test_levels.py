"""Ex-ante levels: every one must be knowable at the bar it is attached to.

The tests that matter here are the availability tests. A level computed from
bars that had not printed yet is hindsight charting, and it is invisible in a
backtest's equity curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.data.sessions import session_date
from qtrader.features.levels import (
    average_true_range,
    level_zone,
    nearest_round_levels,
    opening_range_levels,
    previous_day_levels,
)

MARKET_TZ = "America/New_York"


def two_sessions(bars_per_day: int = 10) -> pd.DatetimeIndex:
    days = [
        pd.date_range(pd.Timestamp(f"2026-08-{day} 09:30", tz=MARKET_TZ),
                      periods=bars_per_day, freq="5min")
        for day in ("03", "04")
    ]
    return days[0].union(days[1]).tz_convert("UTC")


def frames(highs, lows, index=None):
    index = two_sessions() if index is None else index
    return (
        pd.DataFrame({"AAA": list(highs)}, index=index),
        pd.DataFrame({"AAA": list(lows)}, index=index),
    )


# --------------------------------------------------------------- previous day
def test_the_previous_day_level_is_yesterdays_extreme_all_day():
    high, low = frames([10, 12, 11, 9, 10, 10, 10, 10, 10, 10]
                       + [20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
                       [5, 6, 4, 3, 5, 5, 5, 5, 5, 5]
                       + [15, 16, 17, 18, 19, 20, 21, 22, 23, 24])
    top, bottom = previous_day_levels(high, low)

    assert top["AAA"].iloc[:10].isna().all(), "day one has no previous session"
    assert (top["AAA"].iloc[10:] == 12.0).all()
    assert (bottom["AAA"].iloc[10:] == 3.0).all()


def test_the_previous_day_level_never_moves_within_the_session():
    """Today's own extremes must not leak into today's level."""
    high, low = frames(list(range(10)) + list(range(100, 110)),
                       list(range(10)) + list(range(100, 110)))
    top, _ = previous_day_levels(high, low)
    assert top["AAA"].iloc[10:].nunique() == 1


# -------------------------------------------------------------- opening range
def test_the_opening_range_is_unavailable_until_the_window_has_elapsed():
    high, low = frames([1, 5, 3, 2, 4, 2, 9, 9, 9, 9] * 2, [1, 2, 0, 2, 1, 2, 0, 0, 0, 0] * 2)
    top, bottom = opening_range_levels(high, low, bars=6)

    assert top["AAA"].iloc[:6].isna().all(), "level read before the range closed"
    assert (top["AAA"].iloc[6:10] == 5.0).all()
    assert (bottom["AAA"].iloc[6:10] == 0.0).all()


def test_the_opening_range_resets_each_session():
    high, low = frames([1, 5, 3, 2, 4, 2, 9, 9, 9, 9] + [1, 8, 3, 2, 4, 2, 9, 9, 9, 9],
                       [1, 2, 0, 2, 1, 2, 0, 0, 0, 0] + [1, 2, 1, 2, 1, 2, 0, 0, 0, 0])
    top, _ = opening_range_levels(high, low, bars=6)

    assert top["AAA"].iloc[6] == 5.0
    assert top["AAA"].iloc[16] == 8.0


def test_the_opening_range_handles_several_symbols():
    index = two_sessions()
    high = pd.DataFrame({"AAA": np.arange(20.0), "BBB": np.arange(20.0) * 2},
                        index=index)
    top, _ = opening_range_levels(high, high, bars=6)
    assert top.loc[index[6], "AAA"] == 5.0
    assert top.loc[index[6], "BBB"] == 10.0


# --------------------------------------------------------------- round numbers
def test_round_levels_bracket_the_price():
    close = pd.DataFrame({"AAA": [10.4, 10.0, 9.99]}, index=two_sessions()[:3])
    above, below = nearest_round_levels(close, step=1.0)

    assert list(below["AAA"]) == [10.0, 10.0, 9.0]
    assert list(above["AAA"]) == [11.0, 11.0, 10.0]


# ------------------------------------------------------------------------- ATR
def test_atr_uses_the_previous_close_not_the_current_one():
    index = two_sessions()[:4]
    high = pd.DataFrame({"AAA": [10.0, 11.0, 12.0, 13.0]}, index=index)
    low = pd.DataFrame({"AAA": [9.0, 10.0, 11.0, 12.0]}, index=index)
    close = pd.DataFrame({"AAA": [9.5, 10.5, 11.5, 12.5]}, index=index)

    atr = average_true_range(high, low, close, window=2)
    # bar 0 has no previous close, so its true range is just high - low = 1.0.
    # bar 1 is max(11-10, |11-9.5|, |10-9.5|) = 1.5, and the 2-bar mean is 1.25.
    assert atr["AAA"].iloc[0] == pytest.approx(1.0)
    assert atr["AAA"].iloc[1] == pytest.approx(1.25)


def test_the_zone_never_collapses_below_a_tick():
    atr = pd.DataFrame({"AAA": [0.0, 1.0]}, index=two_sessions()[:2])
    close = pd.DataFrame({"AAA": [10.0, 10.0]}, index=atr.index)
    zone = level_zone(atr, close, level_atr=0.1, tick=0.01)

    assert zone["AAA"].iloc[0] == pytest.approx(0.01)
    assert zone["AAA"].iloc[1] == pytest.approx(0.10)


def test_the_true_range_does_not_cross_a_session_boundary():
    """Otherwise the overnight gap sets the width that defines a break."""
    index = two_sessions(bars_per_day=4)
    high = pd.DataFrame({"AAA": [10.0, 10.5, 10.5, 10.5] + [30.0, 30.5, 30.5, 30.5]},
                        index=index)
    low = pd.DataFrame({"AAA": [9.0, 9.5, 9.5, 9.5] + [29.0, 29.5, 29.5, 29.5]}, index=index)
    close = pd.DataFrame({"AAA": [9.5, 10.0, 10.0, 10.0] + [29.5, 30.0, 30.0, 30.0]},
                         index=index)
    day = pd.Series(session_date(index).to_numpy(), index=index)

    leaky = average_true_range(high, low, close, window=2)
    clean = average_true_range(high, low, close, window=2, restart=day)

    # bar 4 opens a new session 20 points higher.
    assert leaky["AAA"].iloc[4] > 5.0, "the gap leaks in without a restart"
    assert clean["AAA"].iloc[4] == pytest.approx(1.0), "high - low only"
