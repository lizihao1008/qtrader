"""Aligning a fine timeframe onto a coarse one — the leakage boundary.

A 5-minute bar labelled T closes at T+5min, and the strategy decides at that
close. The fine bars that exist then are T..T+4. Taking the bar labelled T+5
would hand the strategy the first minute of the bar it is about to be filled on,
and nothing downstream would notice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.multiframe import align_to_coarse

MARKET_TZ = "America/New_York"


def grid(n: int, freq: str, start: str = "2026-06-01 09:30") -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(start, tz=MARKET_TZ), periods=n,
                         freq=freq).tz_convert("UTC")


def fine_frame(n: int, values=None) -> pd.DataFrame:
    index = grid(n, "1min")
    return pd.DataFrame({"AAA": np.arange(n, dtype=float) if values is None else values},
                        index=index)


def test_a_coarse_bar_takes_the_last_fine_bar_inside_its_own_span():
    coarse = grid(4, "5min")
    aligned = align_to_coarse(fine_frame(20), coarse)

    # bar 0 spans minutes 0..4 and closes after minute 4
    assert list(aligned["AAA"]) == [4.0, 9.0, 14.0, 19.0]


def test_the_fine_bar_that_opens_the_next_coarse_bar_is_never_used():
    """The one mistake that would silently leak."""
    coarse = grid(3, "5min")
    fine = fine_frame(15)
    aligned = align_to_coarse(fine, coarse)

    for position, stamp in enumerate(coarse):
        used = aligned["AAA"].iloc[position]
        # every fine bar at or after this coarse bar's close must be excluded
        after = fine.loc[fine.index >= stamp + pd.Timedelta("5min"), "AAA"]
        assert used not in set(after.to_numpy())


def test_a_fine_bar_exactly_on_a_boundary_belongs_to_the_bar_it_opens():
    coarse = grid(2, "5min")
    fine = pd.DataFrame({"AAA": [1.0, 2.0]},
                        index=pd.DatetimeIndex([coarse[0], coarse[1]]))
    aligned = align_to_coarse(fine, coarse)
    assert list(aligned["AAA"]) == [1.0, 2.0]


def test_a_coarse_bar_with_no_fine_data_is_nan_not_zero():
    coarse = grid(3, "5min")
    fine = fine_frame(15)
    fine = fine.drop(fine.index[5:10])          # the whole second coarse bar

    aligned = align_to_coarse(fine, coarse)
    assert np.isnan(aligned["AAA"].iloc[1])
    assert aligned["AAA"].iloc[0] == 4.0 and aligned["AAA"].iloc[2] == 14.0


def test_fine_bars_outside_the_coarse_window_are_ignored():
    coarse = grid(2, "5min")
    fine = fine_frame(30)                        # runs well past the coarse grid
    aligned = align_to_coarse(fine, coarse)

    assert list(aligned["AAA"]) == [4.0, 9.0]
    assert len(aligned) == len(coarse)


def test_fine_bars_before_the_coarse_window_are_ignored():
    coarse = grid(2, "5min", start="2026-06-01 09:40")
    fine = fine_frame(20)                        # starts at 09:30
    aligned = align_to_coarse(fine, coarse)

    assert list(aligned["AAA"]) == [14.0, 19.0]


def test_the_result_is_on_the_coarse_index():
    coarse = grid(6, "5min")
    aligned = align_to_coarse(fine_frame(30), coarse)
    pd.testing.assert_index_equal(aligned.index, coarse)


def test_a_single_coarse_bar_is_refused():
    with pytest.raises(ValueError, match="two coarse bars"):
        align_to_coarse(fine_frame(5), grid(1, "5min"))


# ------------------------------------------------- the strategy-level guarantee
def test_the_fine_confirmation_cannot_see_past_the_coarse_decision_bar():
    """Perturbing fine bars at or after a coarse bar's close must not change it.

    This is the end-to-end version of the boundary test above: it exercises the
    alignment through whatever the caller does with it, so a later refactor that
    shifts the grid the wrong way fails here even if the unit test is edited.
    """
    coarse = grid(8, "5min")
    fine = fine_frame(40, values=np.zeros(40))

    baseline = align_to_coarse(fine, coarse)

    tampered = fine.copy()
    cut = 3                                       # from coarse bar 3 onward
    tampered.loc[tampered.index >= coarse[cut], "AAA"] = 999.0
    after = align_to_coarse(tampered, coarse)

    pd.testing.assert_series_equal(
        baseline["AAA"].iloc[:cut], after["AAA"].iloc[:cut]
    )
    assert (after["AAA"].iloc[cut:] == 999.0).all(), "the tamper never landed"


# --------------------------------------------- the other direction: coarse -> fine
def test_a_coarse_value_is_not_visible_until_its_bar_has_closed():
    """The dangerous direction. Broadcasting T back over T..T+4 is five minutes
    of hindsight on the first of those minutes."""
    from qtrader.features.multiframe import align_to_fine

    coarse = grid(3, "5min")
    fine = grid(15, "1min")
    values = pd.DataFrame({"AAA": [10.0, 20.0, 30.0]}, index=coarse)

    aligned = align_to_fine(values, fine)

    # minutes 0..4 are inside the first coarse bar: it has not closed yet
    assert aligned["AAA"].iloc[:5].isna().all()
    # it becomes visible at minute 5, and holds until the next one closes
    assert (aligned["AAA"].iloc[5:10] == 10.0).all()
    assert (aligned["AAA"].iloc[10:15] == 20.0).all()


def test_no_fine_bar_ever_sees_a_coarse_bar_it_precedes():
    from qtrader.features.multiframe import align_to_fine

    coarse = grid(4, "5min")
    fine = grid(20, "1min")
    values = pd.DataFrame({"AAA": np.arange(4.0)}, index=coarse)
    aligned = align_to_fine(values, fine)

    for stamp, value in aligned["AAA"].dropna().items():
        source = coarse[int(value)]
        assert stamp >= source + pd.Timedelta("5min"), (
            f"{stamp} saw a coarse bar that closed at {source + pd.Timedelta('5min')}"
        )


def test_the_round_trip_does_not_smuggle_information_backwards():
    """fine -> coarse -> fine must never land earlier than where it started."""
    from qtrader.features.multiframe import align_to_fine

    fine_index = grid(30, "1min")
    coarse_index = grid(6, "5min")
    original = fine_frame(30)

    there = align_to_coarse(original, coarse_index)
    back = align_to_fine(there, fine_index)

    for stamp, value in back["AAA"].dropna().items():
        # the value is the last fine observation of some earlier coarse bar
        assert value <= original.loc[stamp, "AAA"], "a future fine value came back"
