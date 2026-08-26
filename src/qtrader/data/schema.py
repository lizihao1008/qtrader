"""Canonical bar schema and validation.

A "bar frame" is the single canonical in-memory representation of OHLCV data
for one symbol:

    index   : pandas.DatetimeIndex, tz-aware UTC, unique, strictly increasing,
              named ``timestamp``. The timestamp is the bar's OPEN time.
    columns : open, high, low, close, volume, trade_count, vwap (all float64)

Every module downstream of :mod:`qtrader.data` may assume these invariants.
They are enforced by :func:`validate_bars`, which is the only place that
decides what "valid data" means.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

INDEX_NAME = "timestamp"

#: Columns required in every bar frame, in canonical order.
REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

#: Columns kept when the provider supplies them, but not required.
OPTIONAL_COLUMNS: tuple[str, ...] = ("trade_count", "vwap")

BAR_COLUMNS: tuple[str, ...] = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


class DataValidationError(ValueError):
    """Raised when a bar frame violates a hard schema/data invariant."""


@dataclass
class ValidationReport:
    """Outcome of :func:`validate_bars`.

    ``errors`` are hard invariant violations (the data must not be used).
    ``warnings`` are shape observations that are expected for real market data
    (e.g. minute gaps outside continuous trading) and are informational only.
    """

    symbol: str
    n_rows: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        head = f"{self.symbol}: {self.n_rows} bars [{self.start} .. {self.end}]"
        lines = [head]
        lines += [f"  ERROR   {m}" for m in self.errors]
        lines += [f"  WARNING {m}" for m in self.warnings]
        return "\n".join(lines)


def coerce_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` reshaped into the canonical bar layout.

    Coercion is mechanical only (column selection, dtypes, index naming,
    UTC conversion, sorting). It never repairs bad values — that is the job of
    :func:`validate_bars`, which must be able to see the problems.
    """
    out = df.copy()

    if INDEX_NAME in out.columns:
        out = out.set_index(INDEX_NAME)

    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        raise DataValidationError(
            "bar index is timezone-naive; provider timestamps must carry a timezone"
        )
    out.index = index.tz_convert("UTC")
    out.index.name = INDEX_NAME

    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise DataValidationError(f"missing required columns: {missing}")

    keep = [c for c in BAR_COLUMNS if c in out.columns]
    out = out[keep].astype("float64")
    return out.sort_index()


def validate_bars(
    bars: pd.DataFrame,
    symbol: str,
    *,
    expected_interval: pd.Timedelta | None = None,
    strict: bool = True,
) -> ValidationReport:
    """Check the canonical invariants of a bar frame.

    Parameters
    ----------
    bars:
        Frame already passed through :func:`coerce_bars`.
    symbol:
        Only used for reporting.
    expected_interval:
        If given, timestamp gaps larger than this are reported as *warnings*
        (intraday data legitimately gaps across the overnight break).
    strict:
        Raise :class:`DataValidationError` when any hard error is found.
    """
    report = ValidationReport(
        symbol=symbol,
        n_rows=len(bars),
        start=bars.index.min() if len(bars) else None,
        end=bars.index.max() if len(bars) else None,
    )
    err = report.errors.append
    warn = report.warnings.append

    if not isinstance(bars.index, pd.DatetimeIndex):
        err("index is not a DatetimeIndex")
    elif bars.index.tz is None:
        err("index is timezone-naive (timestamps must be UTC)")
    elif str(bars.index.tz) != "UTC":
        err(f"index timezone is {bars.index.tz}, expected UTC")

    if len(bars) == 0:
        err("frame is empty")
        if strict:
            raise DataValidationError(report.summary())
        return report

    if bars.index.has_duplicates:
        n_dupes = int(bars.index.duplicated().sum())
        err(f"{n_dupes} duplicate timestamps")
    if not bars.index.is_monotonic_increasing:
        err("timestamps are not sorted ascending")

    for col in REQUIRED_COLUMNS:
        n_bad = int((~bars[col].notna()).sum())
        if n_bad:
            err(f"column '{col}' has {n_bad} NaN/inf values")

    ohlc_high = bars[["open", "close", "low"]].max(axis=1)
    ohlc_low = bars[["open", "close", "high"]].min(axis=1)
    n_high = int((bars["high"] < ohlc_high).sum())
    n_low = int((bars["low"] > ohlc_low).sum())
    if n_high:
        err(f"{n_high} bars where high < max(open, close, low)")
    if n_low:
        err(f"{n_low} bars where low > min(open, close, high)")

    n_nonpos = int((bars[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    if n_nonpos:
        err(f"{n_nonpos} bars with non-positive prices")

    n_neg_vol = int((bars["volume"] < 0).sum())
    if n_neg_vol:
        err(f"{n_neg_vol} bars with negative volume")

    if expected_interval is not None and len(bars) > 1:
        gaps = bars.index.to_series().diff().dropna()
        n_gaps = int((gaps > expected_interval).sum())
        if n_gaps:
            warn(
                f"{n_gaps} gaps larger than {expected_interval} "
                f"(largest {gaps.max()}) — expected across session breaks"
            )

    if strict and report.errors:
        raise DataValidationError(report.summary())
    return report
