"""Cost model — backtests are never frictionless."""

from __future__ import annotations

import pytest

from qtrader.backtest.costs import BUY, SELL, CostModel


def test_buy_pays_up_and_sell_receives_less():
    model = CostModel(half_spread_bps=10.0, slippage_bps=0.0)
    assert model.fill_price(100.0, BUY) == pytest.approx(100.10)
    assert model.fill_price(100.0, SELL) == pytest.approx(99.90)


def test_slippage_adds_to_the_half_spread():
    model = CostModel(half_spread_bps=1.0, slippage_bps=4.0)
    assert model.impact_bps == 5.0
    assert model.fill_price(200.0, BUY) == pytest.approx(200.0 * 1.0005)


def test_commission_combines_per_share_and_notional_with_a_floor():
    model = CostModel(commission_per_share=0.005, commission_bps=1.0, min_commission=1.0)
    assert model.commission(1000, 100_000.0) == pytest.approx(5.0 + 10.0)
    assert model.commission(10, 1_000.0) == pytest.approx(1.0)  # floor applies
    assert model.commission(0, 0.0) == 0.0


def test_invalid_side_is_rejected():
    with pytest.raises(ValueError):
        CostModel().fill_price(100.0, 0)
