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

from .alpaca_client import AlpacaBarClient, UnknownSymbolError
from .panel import BarPanel
from .schema import DataValidationError, ValidationReport, validate_bars
from .sessions import filter_regular_hours, session_date
from .storage import BarStore, DatasetKey

TIMEFRAME_INTERVAL = {
    "1Min": pd.Timedelta(minutes=1),
    "5Min": pd.Timedelta(minutes=5),
    "15Min": pd.Timedelta(minutes=15),
    "1Hour": pd.Timedelta(hours=1),
    "1Day": pd.Timedelta(days=1),
}

#: When a symbol has never been stored, SessionLab fetches this much history
#: so the next day does not need another round trip. Two calendar years.
MISSING_LOOKBACK = dt.timedelta(days=730)


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


def drop_incomplete_bars(
    bars: pd.DataFrame, timeframe: str, *, now: dt.datetime | None = None
) -> pd.DataFrame:
    """Discard any bar whose interval has not finished forming.

    A bar timestamped ``t`` covers ``[t, t + interval)``, so it is only a fact
    once ``now >= t + interval``. Fetched before then it is a *partial* bar: its
    close, high, low and volume are whatever had happened so far. Storing one
    puts a bar in the dataset that never existed — the mirror image of
    look-ahead, and just as capable of inventing a signal.

    This is not hypothetical. A 5-minute bar captured mid-interval was stored
    with close 313.275 on volume 3,264; the completed bar was 313.685 on 7,018.
    It also makes the raw layer's immutability guard fire on the next download,
    which is how it was found.
    """
    interval = TIMEFRAME_INTERVAL.get(timeframe)
    if interval is None or bars.empty:
        return bars
    now = now or dt.datetime.now(dt.timezone.utc)
    return bars.loc[bars.index + interval <= pd.Timestamp(now)]


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
    raw = drop_incomplete_bars(raw, timeframe)

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


def ensure_bars(
    symbols: Sequence[str],
    start: dt.datetime,
    end: dt.datetime,
    *,
    timeframe: str = "1Min",
    feed: str = "iex",
    store: BarStore | None = None,
    client: AlpacaBarClient | None = None,
    regular_hours_only: bool = True,
    lookback: dt.timedelta = MISSING_LOOKBACK,
    long_lookback_for: Sequence[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Download any symbol whose local clean window does not cover ``[start, end)``.

    A symbol with **no file at all** is fetched from ``end - lookback`` when it
    is in ``long_lookback_for`` (the name the lab was asked for); other missing
    universe members only fetch the requested window, so opening the lab does
    not silently pull two years of the whole book.

    An empty provider response on a **first** fetch means the name is not listed
    on this feed: :class:`UnknownSymbolError`. A stored symbol whose target
    session is a holiday may fetch nothing; that is kept, not treated as unknown.
    Backtests still go through :func:`load_panel`, which refuses missing files —
    auto-download is lab-only.
    """
    store = store or BarStore()
    wanted = list(dict.fromkeys(symbols))
    extra = set(long_lookback_for or ())
    fetched: list[str] = []
    for symbol in wanted:
        key = DatasetKey(symbol=symbol, timeframe=timeframe, feed=feed)
        fetch_start, fetch_end = _fetch_window(
            store, key, start, end, lookback=lookback, use_lookback=symbol in extra
        )
        if fetch_start is None:
            continue
        if client is None:
            client = AlpacaBarClient()
        result = ingest_symbols(
            [symbol],
            fetch_start,
            fetch_end,
            timeframe=timeframe,
            feed=feed,
            store=store,
            client=client,
            regular_hours_only=regular_hours_only,
        )[0]
        if result.clean_rows == 0:
            # A name we already store may have a holiday gap; that is not
            # "unknown". Only a first-time empty fetch means the ticker is not
            # on this feed.
            if store.exists(key, "clean"):
                continue
            raise UnknownSymbolError(
                f"{symbol!r} is not listed on Alpaca for {timeframe} {feed}, "
                f"or the feed returned no bars in {fetch_start.date()} → {fetch_end.date()}"
            )
        fetched.append(symbol)
        if on_progress is not None:
            on_progress(symbol)
    return fetched


def _fetch_window(
    store: BarStore,
    key: DatasetKey,
    start: dt.datetime,
    end: dt.datetime,
    *,
    lookback: dt.timedelta,
    use_lookback: bool,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """``(fetch_start, fetch_end)`` or ``(None, None)`` if the store already covers it.

    Overlap with the warmup window is not enough. Cross-sectional z-scores need
    the *target* session on every name; if the book stops a week earlier and
    only the lab symbol was gap-filled, that session has N=1 and every z is NaN.
    """
    start = to_utc(start).to_pydatetime()
    end = to_utc(end).to_pydatetime()
    if not store.exists(key, "clean"):
        fetch_start = min(start, end - lookback) if use_lookback else start
        return fetch_start, end
    bars = store.read(key, "clean")
    sliced = bars.loc[(bars.index >= start) & (bars.index < end)]
    if sliced.empty:
        return start, end
    target_day = to_utc(end).date() - dt.timedelta(days=1)
    have_target = bool((session_date(bars.index).to_numpy() == target_day).any())
    if not have_target:
        have_end = bars.index.max().to_pydatetime()
        if have_end.tzinfo is None:
            have_end = have_end.replace(tzinfo=dt.timezone.utc)
        # Store stopped before this window: gap-fill. Store already extends
        # past `end` but this calendar day has no session (weekend/holiday):
        # downloading will not create bars, so leave it and let the lab error.
        if have_end < end:
            return have_end, end
    return None, None


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
