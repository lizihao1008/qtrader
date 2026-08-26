"""Shared deterministic fixtures.

Bars are synthetic and tiny so every expected number in the tests can be
derived by hand.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.data.panel import BarPanel
from qtrader.data.schema import coerce_bars
from qtrader.strategies.base import MarketContext
from qtrader.universe.definition import Universe

MARKET_TZ = "America/New_York"


def minute_index(n: int, start_local: str = "2026-08-03 09:30") -> pd.DatetimeIndex:
    """``n`` consecutive 1-minute timestamps inside one regular session."""
    start = pd.Timestamp(start_local, tz=MARKET_TZ)
    return pd.date_range(start, periods=n, freq="1min").tz_convert("UTC")


def make_bars(prices, *, opens=None, volume: float = 1_000.0, index=None) -> pd.DataFrame:
    """Bar frame from a close-price path; ``opens`` defaults to the closes."""
    closes = list(prices)
    opens = list(opens) if opens is not None else closes
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [volume] * len(closes),
        },
        index=minute_index(len(closes)) if index is None else index,
    )
    return coerce_bars(frame)


def make_panel(prices: dict[str, list[float]], *, opens: dict | None = None) -> BarPanel:
    """Panel from ``{symbol: close path}``, all symbols on the same index."""
    opens = opens or {}
    return BarPanel.from_frames(
        {symbol: make_bars(path, opens=opens.get(symbol)) for symbol, path in prices.items()}
    )


def make_context(
    prices: dict[str, list[float]],
    *,
    symbols: list[str] | None = None,
    benchmark: str = "SPY",
    sectors: dict[str, str] | None = None,
    opens: dict | None = None,
    tradable: pd.DataFrame | None = None,
) -> MarketContext:
    """A ready-to-use context; every listed symbol is tradable unless overridden."""
    panel = make_panel(prices, opens=opens)
    tradable_symbols = symbols or [s for s in panel.symbols if s != benchmark]
    universe = Universe(
        name="test",
        symbols=tuple(tradable_symbols),
        benchmark=benchmark,
        sectors=sectors or {},
    )
    if tradable is None:
        tradable = pd.DataFrame(True, index=panel.index, columns=tradable_symbols)
    return MarketContext(panel=panel, universe=universe, tradable=tradable)


@pytest.fixture
def flat_bars() -> pd.DataFrame:
    return make_bars([100.0] * 10)


@pytest.fixture
def trending_bars() -> pd.DataFrame:
    return make_bars([100.0 + i for i in range(10)])
