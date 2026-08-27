"""Schema invariants: the data layer must reject broken market data."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.data.schema import DataValidationError, coerce_bars, validate_bars
from tests.conftest import make_bars, minute_index


def test_coerce_converts_to_utc_and_canonical_columns():
    bars = make_bars([10.0, 11.0])
    assert str(bars.index.tz) == "UTC"
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert bars.index.name == "timestamp"


def test_naive_timestamps_are_rejected():
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=pd.DatetimeIndex(["2026-08-03 13:30"], name="timestamp"),
    )
    with pytest.raises(DataValidationError, match="timezone-naive"):
        coerce_bars(frame)


def test_valid_bars_pass():
    report = validate_bars(make_bars([10.0, 10.5, 10.2]), "TEST")
    assert report.ok


def test_duplicate_timestamps_are_errors():
    bars = make_bars([10.0, 10.0])
    duplicated = pd.concat([bars, bars.iloc[[0]]]).sort_index()
    with pytest.raises(DataValidationError, match="duplicate timestamps"):
        validate_bars(duplicated, "TEST")


def test_impossible_ohlc_is_an_error():
    bars = make_bars([10.0, 11.0]).copy()
    bars.iloc[1, bars.columns.get_loc("high")] = 9.0  # high below close
    with pytest.raises(DataValidationError, match="high <"):
        validate_bars(bars, "TEST")


def test_negative_volume_is_an_error():
    bars = make_bars([10.0, 11.0]).copy()
    bars.iloc[0, bars.columns.get_loc("volume")] = -5.0
    with pytest.raises(DataValidationError, match="negative volume"):
        validate_bars(bars, "TEST")


def test_session_gaps_are_warnings_not_errors():
    bars = make_bars([10.0, 10.1, 10.2])
    with_gap = bars.drop(bars.index[1])
    report = validate_bars(with_gap, "TEST", expected_interval=pd.Timedelta(minutes=1))
    assert report.ok
    assert report.warnings


def test_empty_frame_is_an_error():
    empty = make_bars([]).reindex(minute_index(0))
    with pytest.raises(DataValidationError, match="empty"):
        validate_bars(empty, "TEST")


def test_a_bar_whose_interval_has_not_closed_is_not_stored():
    """A partial bar is not a fact about the market; storing one invents data."""
    import datetime as dt

    from qtrader.data.ingest import drop_incomplete_bars

    bars = make_bars([10.0, 11.0, 12.0])  # 09:30, 09:31, 09:32 New York
    # 09:32:30 — the 09:32 bar is still forming, the earlier two are complete.
    now = bars.index[2] + pd.Timedelta(seconds=30)

    kept = drop_incomplete_bars(bars, "1Min", now=now)
    assert list(kept.index) == list(bars.index[:2])

    settled = drop_incomplete_bars(bars, "1Min", now=bars.index[2] + pd.Timedelta(minutes=1))
    assert len(settled) == 3
