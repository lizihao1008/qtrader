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


class UnknownSymbolError(ValueError):
    """The provider does not list this name, so there is nothing to download."""


#: Names people type that are not Alpaca tickers. There is no Nikkei 225 listing.
#: ``JPXN`` (JPX-Nikkei 400) is the only US name that contains "Nikkei", but IEX
#: prints it a few times a day — unusable as a minute series. ``EWJ`` (MSCI
#: Japan) is the liquid Japan ETF on this feed, so that is what the lab fetches.
SYMBOL_ALIASES = {
    "NIKKEI": "EWJ",
    "NIKKEI225": "EWJ",
    "N225": "EWJ",
    "NKY": "EWJ",
}


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


def resolve_symbol(
    name: str,
    *,
    lookup=None,
    search_names=None,
) -> str:
    """Map a user-typed name to an Alpaca ticker.

    Order: strip/upper, apply :data:`SYMBOL_ALIASES`, then (only if ``lookup``
    or ``search_names`` is supplied, or the name is not ticker-shaped) ask the
    provider. A ticker-shaped name is returned as-is so the lab can use a
    local parquet without hitting the network. Unknown names fail later, when
    a download returns nothing.
    """
    raw = name.strip()
    if not raw:
        raise UnknownSymbolError("empty symbol")
    ticker = SYMBOL_ALIASES.get(raw.upper(), raw.upper())
    if lookup is None and search_names is None:
        return ticker

    found = lookup(ticker) if lookup is not None else None
    if found:
        return found
    if search_names is None:
        raise UnknownSymbolError(
            f"{name!r} is not listed on Alpaca under {ticker!r}"
        )
    hits = list(search_names(raw))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise UnknownSymbolError(
            f"{name!r} is not a ticker; Alpaca name matches: {', '.join(hits)}. "
            "Pass one of those symbols."
        )
    raise UnknownSymbolError(f"{name!r} is not listed on Alpaca")


def alpaca_lookup(symbol: str) -> str | None:
    """``get_asset``; ``None`` on 404."""
    from alpaca.common.exceptions import APIError

    try:
        asset = _trading_client().get_asset(symbol)
    except APIError as exc:
        if "404" in str(exc):
            return None
        raise
    return asset.symbol


def alpaca_search_names(query: str) -> list[str]:
    """Active, tradable US names whose description contains ``query``."""
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    needle = query.strip().lower()
    if not needle:
        return []
    assets = _trading_client().get_all_assets(
        GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    )
    return sorted(
        {
            a.symbol
            for a in assets
            if a.tradable and a.name and needle in a.name.lower()
        }
    )


def _trading_client():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise AlpacaCredentialsError(
            "set ALPACA_API_KEY and ALPACA_SECRET_KEY in the environment"
        )
    from alpaca.trading.client import TradingClient

    return TradingClient(api_key, secret_key, paper=True)


def _empty_bars() -> pd.DataFrame:
    """Canonical frame with no rows, for symbols the provider had no data for."""
    return coerce_bars(
        pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
        )
    )
