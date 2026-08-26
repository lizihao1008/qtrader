"""Position, cash and round-trip accounting across symbols."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.backtest.costs import CostModel
from qtrader.backtest.portfolio import Portfolio

FREE = CostModel(half_spread_bps=0.0, slippage_bps=0.0)
T0 = pd.Timestamp("2026-08-03 13:30", tz="UTC")
T1 = T0 + pd.Timedelta(minutes=5)


def test_long_round_trip_pnl_and_cash():
    book = Portfolio(10_000.0, FREE)
    book.rebalance_to("AAA", 100, timestamp=T0, reference_price=50.0)
    assert book.cash == pytest.approx(5_000.0)
    assert book.equity({"AAA": 50.0}) == pytest.approx(10_000.0)

    book.rebalance_to("AAA", 0, timestamp=T1, reference_price=52.0)
    assert book.cash == pytest.approx(10_200.0)

    trade = book.trades[0]
    assert (trade.symbol, trade.direction) == ("AAA", 1)
    assert trade.net_pnl == pytest.approx(200.0)
    assert trade.return_pct == pytest.approx(200.0 / 5_000.0)


def test_short_round_trip_profits_when_price_falls():
    book = Portfolio(10_000.0, FREE)
    book.rebalance_to("AAA", -100, timestamp=T0, reference_price=50.0)
    assert book.cash == pytest.approx(15_000.0)
    assert book.equity({"AAA": 50.0}) == pytest.approx(10_000.0)

    book.rebalance_to("AAA", 0, timestamp=T1, reference_price=48.0)
    assert book.trades[0].direction == -1
    assert book.trades[0].net_pnl == pytest.approx(200.0)


def test_positions_in_different_symbols_are_booked_independently():
    book = Portfolio(10_000.0, FREE)
    book.rebalance_to("AAA", 100, timestamp=T0, reference_price=50.0)
    book.rebalance_to("BBB", -50, timestamp=T0, reference_price=20.0)

    assert book.shares("AAA") == 100
    assert book.shares("BBB") == -50
    assert book.equity({"AAA": 50.0, "BBB": 20.0}) == pytest.approx(10_000.0)

    book.rebalance_to("AAA", 0, timestamp=T1, reference_price=51.0)
    assert [t.symbol for t in book.trades] == ["AAA"]  # BBB is still open
    assert book.shares("BBB") == -50


def test_costs_are_charged_to_the_trade():
    model = CostModel(half_spread_bps=10.0, slippage_bps=0.0, commission_per_share=0.01)
    book = Portfolio(10_000.0, model)
    book.rebalance_to("AAA", 100, timestamp=T0, reference_price=50.0)
    book.rebalance_to("AAA", 0, timestamp=T1, reference_price=50.0)

    trade = book.trades[0]
    # Reference price unchanged -> no gross PnL, and the loss is exactly the costs.
    assert trade.gross_pnl == pytest.approx(0.0)
    assert trade.slippage_cost == pytest.approx(2 * 100 * 0.05)  # 10 bps of $50, both legs
    assert trade.commission == pytest.approx(2 * 100 * 0.01)
    assert trade.net_pnl == pytest.approx(-12.0)
    assert book.cash - 10_000.0 == pytest.approx(trade.net_pnl)


def test_reversal_closes_one_trade_and_opens_the_next():
    book = Portfolio(10_000.0, FREE)
    book.rebalance_to("AAA", 100, timestamp=T0, reference_price=50.0)
    book.rebalance_to("AAA", -100, timestamp=T1, reference_price=52.0)

    assert len(book.trades) == 1
    assert book.trades[0].net_pnl == pytest.approx(200.0)
    assert book.shares("AAA") == -100

    book.rebalance_to("AAA", 0, timestamp=T1 + pd.Timedelta(minutes=5), reference_price=51.0)
    assert len(book.trades) == 2
    assert book.trades[1].direction == -1
    assert book.trades[1].net_pnl == pytest.approx(100.0)


def test_no_fill_when_already_at_target():
    book = Portfolio(10_000.0, FREE)
    assert book.rebalance_to("AAA", 0, timestamp=T0, reference_price=50.0) is None
    assert book.fills == []
