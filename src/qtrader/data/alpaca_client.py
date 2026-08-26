"""Thin Alpaca market-data client.

Responsibilities are deliberately narrow: authenticate, request bars, and
return the provider's answer in the canonical schema. No validation, no
storage, no feature logic — those live in :mod:`qtrader.data.schema`,
:mod:`qtrader.data.storage` and :mod:`qtrader.features`.

Credentials come from the environment (``ALPACA_API_KEY`` /
``ALPACA_SECRET_KEY``) and must never be written to config files or Git.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Sequence

import pandas as pd

from .schema import coerce_bars

#: Provider timeframe strings supported by the ingest path.
SUPPORTED_TIMEFRAMES = ("1Min", "5Min", "15Min", "1Hour", "1Day")


class AlpacaCredentialsError(RuntimeError):
    """Raised when API credentials are not available in the environment."""


def _parse_timeframe(timeframe: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    table = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame(1, TimeFrameUnit.Day),
    }
    if timeframe not in table:
        raise ValueError(
            f"unsupported timeframe {timeframe!r}; expected one of {SUPPORTED_TIMEFRAMES}"
        )
    return table[timeframe]


class AlpacaBarClient:
    """Fetch historical OHLCV bars from Alpaca in the canonical schema."""

    def __init__(self, api_key: str | None = None, secret_key: str | None = None):
        api_key = api_key or os.environ.get("ALPACA_API_KEY")
        secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise AlpacaCredentialsError(
                "set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment"
            )

        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(api_key, secret_key)

    def fetch_bars(
        self,
        symbol: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        timeframe: str = "1Min",
        feed: str = "iex",
        adjustment: str = "raw",
    ) -> pd.DataFrame:
        """Return one symbol's bars in ``[start, end)`` as a canonical frame."""
        return self.fetch_bars_multi(
            [symbol], start, end, timeframe=timeframe, feed=feed, adjustment=adjustment
        )[symbol]

    def fetch_bars_multi(
        self,
        symbols: Sequence[str],
        start: dt.datetime,
        end: dt.datetime,
        *,
        timeframe: str = "1Min",
        feed: str = "iex",
        adjustment: str = "raw",
    ) -> dict[str, pd.DataFrame]:
        """Return canonical bar frames for several symbols in ``[start, end)``.

        Alpaca accepts many symbols per request, which is far faster than one
        request per symbol. Symbols the provider returns nothing for come back
        as empty frames rather than being silently dropped, so the caller can
        tell "no data" from "not requested".

        ``feed`` is ``iex`` (free tier) or ``sip`` (paid consolidated tape);
        which one produced a dataset is recorded by the storage layer, because
        the two are not interchangeable for research.
        """
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest

        symbols = list(dict.fromkeys(symbols))
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=_parse_timeframe(timeframe),
            start=start,
            end=end,
            feed=DataFeed(feed),
            adjustment=Adjustment(adjustment),
        )
        raw = self._client.get_stock_bars(request).df
        returned = set() if raw.empty else set(raw.index.get_level_values("symbol"))
        return {
            symbol: coerce_bars(raw.xs(symbol, level="symbol"))
            if symbol in returned
            else _empty_bars()
            for symbol in symbols
        }


def _empty_bars() -> pd.DataFrame:
    """Canonical frame with no rows, for symbols the provider had no data for."""
    return coerce_bars(
        pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
        )
    )
