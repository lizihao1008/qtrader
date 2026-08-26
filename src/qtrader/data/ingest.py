"""Ingestion pipeline: provider -> raw -> validated clean dataset.

    fetch (AlpacaBarClient)
        -> validate (schema.validate_bars)
        -> store raw  (immutable, exactly as received)
        -> build clean (regular-hours filter, re-validated)
        -> store clean

Only the clean layer is consumed by features and backtests, and only through
:func:`load_panel`, which aligns every symbol onto one timestamp grid.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pandas as pd

from .alpaca_client import AlpacaBarClient
from .panel import BarPanel
from .schema import DataValidationError, ValidationReport, validate_bars
from .sessions import filter_regular_hours
from .storage import BarStore, DatasetKey

TIMEFRAME_INTERVAL = {
    "1Min": pd.Timedelta(minutes=1),
    "5Min": pd.Timedelta(minutes=5),
    "15Min": pd.Timedelta(minutes=15),
    "1Hour": pd.Timedelta(hours=1),
    "1Day": pd.Timedelta(days=1),
}


@dataclass
class IngestResult:
    key: DatasetKey
    raw_rows: int
    clean_rows: int
    raw_report: ValidationReport
    clean_report: ValidationReport

    def summary(self) -> str:
        return (
            f"{self.key.symbol} {self.key.timeframe} ({self.key.feed}): "
            f"raw={self.raw_rows} clean={self.clean_rows}\n"
            f"{self.clean_report.summary()}"
        )


def ingest_symbols(
    symbols: Sequence[str],
    start: dt.datetime,
    end: dt.datetime,
    *,
    timeframe: str = "1Min",
    feed: str = "iex",
    store: BarStore | None = None,
    client: AlpacaBarClient | None = None,
    regular_hours_only: bool = True,
    batch_size: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> list[IngestResult]:
    """Download, validate and persist bars for several symbols.

    Symbols are requested in batches because one multi-symbol request is far
    cheaper than one request per symbol. A symbol the provider has no data for
    is reported with zero rows rather than aborting the run — an empty dataset
    is a fact about the feed, not a crash.
    """
    store = store or BarStore()
    client = client or AlpacaBarClient()
    symbols = list(dict.fromkeys(symbols))
    results: list[IngestResult] = []

    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset : offset + batch_size]
        frames = client.fetch_bars_multi(batch, start, end, timeframe=timeframe, feed=feed)
        for symbol in batch:
            results.append(
                _store_symbol(
                    symbol,
                    frames[symbol],
                    store=store,
                    timeframe=timeframe,
                    feed=feed,
                    requested=(start, end),
                    regular_hours_only=regular_hours_only,
                )
            )
            if on_progress is not None:
                on_progress(symbol)
    return results


def _store_symbol(
    symbol: str,
    raw: pd.DataFrame,
    *,
    store: BarStore,
    timeframe: str,
    feed: str,
    requested: tuple[dt.datetime, dt.datetime],
    regular_hours_only: bool,
) -> IngestResult:
    """Validate and persist one symbol's freshly downloaded bars."""
    key = DatasetKey(symbol=symbol, timeframe=timeframe, feed=feed)
    interval = TIMEFRAME_INTERVAL.get(timeframe)

    if raw.empty:
        empty = validate_bars(raw, symbol, expected_interval=interval, strict=False)
        return IngestResult(key=key, raw_rows=0, clean_rows=0, raw_report=empty, clean_report=empty)

    raw_report = validate_bars(raw, symbol, expected_interval=interval, strict=True)
    store.write_raw(
        key,
        raw,
        requested_start=str(requested[0]),
        requested_end=str(requested[1]),
        warnings=raw_report.warnings,
    )
    clean, clean_report = build_clean(key, store, regular_hours_only=regular_hours_only)
    return IngestResult(
        key=key,
        raw_rows=len(raw),
        clean_rows=len(clean),
        raw_report=raw_report,
        clean_report=clean_report,
    )


def build_clean(
    key: DatasetKey,
    store: BarStore,
    *,
    regular_hours_only: bool = True,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Rebuild the clean layer for ``key`` from the stored raw layer."""
    raw = store.read(key, "raw")
    clean = filter_regular_hours(raw) if regular_hours_only else raw
    report = validate_bars(
        clean,
        key.symbol,
        expected_interval=TIMEFRAME_INTERVAL.get(key.timeframe),
        strict=True,
    )
    store.write_clean(
        key,
        clean,
        regular_hours_only=regular_hours_only,
        warnings=report.warnings,
    )
    return clean, report


def load_panel(
    symbols: Sequence[str],
    *,
    timeframe: str = "1Min",
    feed: str = "iex",
    start: dt.datetime | str | None = None,
    end: dt.datetime | str | None = None,
    store: BarStore | None = None,
) -> BarPanel:
    """Load every symbol's clean dataset and align it onto one timestamp grid.

    A missing dataset is an error, not a silently smaller universe: dropping a
    symbol because its file is absent quietly changes what the cross-section was
    ranked against.
    """
    store = store or BarStore()
    frames = {}
    for symbol in dict.fromkeys(symbols):
        bars = load_clean_bars(
            symbol, timeframe=timeframe, feed=feed, start=start, end=end, store=store
        )
        if bars.empty:
            raise DataValidationError(
                f"no clean bars for {symbol} in the requested range; "
                "download the universe before running a backtest"
            )
        frames[symbol] = bars
    return BarPanel.from_frames(frames)


def load_clean_bars(
    symbol: str,
    *,
    timeframe: str = "1Min",
    feed: str = "iex",
    start: dt.datetime | str | None = None,
    end: dt.datetime | str | None = None,
    store: BarStore | None = None,
) -> pd.DataFrame:
    """Load a stored clean dataset, optionally sliced to ``[start, end)``."""
    store = store or BarStore()
    bars = store.read(DatasetKey(symbol=symbol, timeframe=timeframe, feed=feed), "clean")
    if start is not None:
        bars = bars.loc[bars.index >= to_utc(start)]
    if end is not None:
        bars = bars.loc[bars.index < to_utc(end)]
    return bars


def to_utc(moment: dt.datetime | str) -> pd.Timestamp:
    """Interpret a naive datetime/string as UTC; convert an aware one to UTC."""
    ts = pd.Timestamp(moment)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
