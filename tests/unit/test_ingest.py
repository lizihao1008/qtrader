"""Local store coverage and the lab's download-if-missing path."""

from __future__ import annotations

import datetime as dt

import pytest

from qtrader.data.alpaca_client import UnknownSymbolError, _empty_bars, resolve_symbol
from qtrader.data.ingest import ensure_bars
from qtrader.data.storage import BarStore, DatasetKey
from tests.conftest import make_bars


class FakeClient:
    def __init__(self, frames: dict):
        self.frames = frames
        self.calls: list[list[str]] = []

    def fetch_bars_multi(self, symbols, start, end, **kwargs):
        self.calls.append(list(symbols))
        return {s: self.frames.get(s, _empty_bars()) for s in symbols}


def _window():
    start = dt.datetime(2026, 8, 3, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc)
    return start, end


def test_resolve_symbol_aliases_nikkei_without_the_network():
    assert resolve_symbol("Nikkei") == "EWJ"
    assert resolve_symbol("QQQ") == "QQQ"


def test_ensure_downloads_when_the_store_is_empty(tmp_path):
    store = BarStore(tmp_path)
    start, end = _window()
    client = FakeClient({"AAA": make_bars([100.0 + i * 0.1 for i in range(30)])})

    fetched = ensure_bars(
        ["AAA"], start, end, store=store, client=client, long_lookback_for=("AAA",)
    )

    assert fetched == ["AAA"]
    assert store.exists(DatasetKey("AAA", "1Min", "iex"), "clean")
    assert client.calls == [["AAA"]]


def test_ensure_skips_a_window_the_store_already_covers(tmp_path):
    store = BarStore(tmp_path)
    start, end = _window()
    key = DatasetKey("AAA", "1Min", "iex")
    store.write_clean(key, make_bars([100.0] * 30))
    client = FakeClient({})

    fetched = ensure_bars(["AAA"], start, end, store=store, client=client)

    assert fetched == []
    assert client.calls == []


def test_ensure_gap_fills_when_the_target_session_is_missing(tmp_path):
    """Warmup bars in the window are not coverage of the day under study."""
    store = BarStore(tmp_path)
    key = DatasetKey("AAA", "1Min", "iex")
    store.write_clean(key, make_bars([100.0] * 30))  # 2026-08-03 only
    start = dt.datetime(2026, 8, 3, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc)  # target 2026-09-01
    client = FakeClient({"AAA": make_bars([101.0] * 30)})

    fetched = ensure_bars(["AAA"], start, end, store=store, client=client)

    assert fetched == ["AAA"]
    assert client.calls == [["AAA"]]


def test_ensure_raises_when_the_provider_returns_nothing(tmp_path):
    store = BarStore(tmp_path)
    start, end = _window()
    client = FakeClient({})

    with pytest.raises(UnknownSymbolError, match="not listed"):
        ensure_bars(["NOPE"], start, end, store=store, client=client)
