"""Universe definition and time-aware tradability filters."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.universe import LiquidityFilter, Universe
from tests.conftest import make_panel


def build_universe(**kwargs) -> Universe:
    defaults = dict(
        name="test",
        symbols=("AAA", "BBB"),
        benchmark="SPY",
        sectors={"AAA": "XLK", "BBB": "XLK"},
    )
    return Universe(**{**defaults, **kwargs})


def test_reference_symbols_are_downloaded_but_not_traded():
    universe = build_universe()
    assert universe.reference_symbols == ("SPY", "XLK")
    assert universe.all_symbols == ("AAA", "BBB", "SPY", "XLK")
    assert "SPY" not in universe.symbols


def test_with_symbol_adds_a_tradable_name_without_touching_references():
    base = build_universe()
    universe = base.with_symbol("QQQ")
    assert universe.symbols == ("AAA", "BBB", "QQQ")
    assert universe.benchmark == "SPY"
    assert base.with_symbol("AAA") is base


def test_peers_share_a_sector():
    universe = build_universe(
        symbols=("AAA", "BBB", "CCC"),
        sectors={"AAA": "XLK", "BBB": "XLK", "CCC": "XLF"},
    )
    assert universe.peers("AAA") == ("BBB",)
    assert universe.peers("CCC") == ()


def test_sector_map_may_not_name_outsiders():
    with pytest.raises(ValueError, match="outside the universe"):
        build_universe(sectors={"ZZZ": "XLK"})


def test_missing_sectors_fail_only_when_a_strategy_needs_them():
    universe = build_universe(sectors={})
    assert universe.all_symbols  # constructing is fine
    with pytest.raises(ValueError, match="no sector assigned"):
        universe.require_sectors()


def test_illiquid_and_cheap_symbols_are_not_tradable():
    panel = make_panel({"RICH": [100.0] * 10, "CHEAP": [1.0] * 10})
    mask = LiquidityFilter(
        min_price=5.0, min_dollar_volume=1_000.0, lookback_bars=3, max_stale_bars=5
    ).tradable(panel)

    assert mask["RICH"].iloc[3:].all()
    assert not mask["CHEAP"].any()  # below the price floor everywhere


def test_stale_symbols_are_not_tradable():
    fresh = make_panel({"AAA": [50.0] * 12, "BBB": [50.0] * 12})
    stale = fresh.subset(["AAA", "BBB"])
    # Blank out BBB's prints after bar 5: it still has a price, but no trades.
    stale.traded.loc[stale.index[6:], "BBB"] = False

    mask = LiquidityFilter(
        min_price=1.0, min_dollar_volume=0.0, lookback_bars=3, max_stale_bars=2
    ).tradable(stale)
    assert mask["AAA"].iloc[-1]
    assert not mask["BBB"].iloc[-1]


def test_the_filter_never_looks_ahead():
    prices = [50.0] * 10
    panel = make_panel({"AAA": prices, "BBB": prices})
    liquidity = LiquidityFilter(lookback_bars=3, min_dollar_volume=0.0, max_stale_bars=3)

    tampered = make_panel({"AAA": prices[:5] + [500.0] * 5, "BBB": prices})
    pd.testing.assert_series_equal(
        liquidity.tradable(panel)["AAA"].iloc[:5],
        liquidity.tradable(tampered)["AAA"].iloc[:5],
    )
