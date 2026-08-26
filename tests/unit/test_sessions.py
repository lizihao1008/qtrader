"""Session/timezone logic — the boundary where UTC storage meets NY hours."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from qtrader.data.sessions import (
    at_or_after_market_time,
    filter_regular_hours,
    session_date,
)


def _utc(*local_times: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(
        [pd.Timestamp(t, tz="America/New_York") for t in local_times]
    ).tz_convert("UTC")


def test_regular_hours_filter_drops_extended_hours_and_weekends():
    index = _utc(
        "2026-08-03 08:00",  # pre-market
        "2026-08-03 09:30",  # first RTH bar
        "2026-08-03 15:59",  # last RTH bar
        "2026-08-03 16:00",  # close is exclusive
        "2026-08-03 18:30",  # after-hours
        "2026-08-01 11:00",  # Saturday
    )
    bars = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index)
    kept = filter_regular_hours(bars)
    assert list(kept.index) == list(_utc("2026-08-03 09:30", "2026-08-03 15:59"))


def test_session_date_uses_exchange_local_calendar():
    # 2026-08-03 20:00 UTC is still the 3rd in New York (16:00 EDT).
    index = pd.DatetimeIndex(["2026-08-03T20:00:00Z", "2026-08-04T13:30:00Z"])
    assert list(session_date(index)) == [dt.date(2026, 8, 3), dt.date(2026, 8, 4)]


def test_flat_time_marks_end_of_session_only():
    index = _utc("2026-08-03 09:30", "2026-08-03 15:54", "2026-08-03 15:55", "2026-08-03 15:59")
    mask = at_or_after_market_time(index, dt.time(15, 55))
    assert list(mask) == [False, False, True, True]
