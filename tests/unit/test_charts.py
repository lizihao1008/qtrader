"""Chart construction, including the thinning that keeps long runs openable."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.backtest.costs import CostModel
from qtrader.backtest.engine import BacktestEngine, ExecutionConfig
from qtrader.strategies.base import StrategySignals
from qtrader.viz.charts import MAX_CANDLES, equity_chart, exposure_chart, price_chart
from tests.conftest import make_context

FREE = CostModel(half_spread_bps=0.0, slippage_bps=0.0)


def run(n_bars: int, weights: list[float] | None = None):
    prices = [100.0 + (i % 11) * 0.5 for i in range(n_bars)]
    context = make_context({"AAA": prices, "SPY": [400.0] * n_bars})
    if weights is None:
        weights = [1.0 if (i // 20) % 2 else 0.0 for i in range(n_bars)]
    signals = StrategySignals(
        target_weights=pd.DataFrame({"AAA": weights}, index=context.index, dtype=float)
    )
    return BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0)).run(context, signals)


def test_price_chart_marks_both_sides_of_every_round_trip():
    result = run(120)
    names = {trace.name for trace in price_chart(result, "AAA").data}
    assert {"AAA", "BUY fill", "SELL fill"} <= names


def test_price_chart_charts_only_the_most_recent_window():
    result = run(MAX_CANDLES + 500)
    figure = price_chart(result, "AAA")
    candles = next(t for t in figure.data if t.type == "candlestick")

    assert len(candles.x) == MAX_CANDLES
    assert "last" in figure.layout.title.text
    # Markers must be clipped to the charted window, not left dangling off-axis.
    for trace in figure.data:
        if trace.name and trace.name.endswith("fill"):
            assert min(trace.x) >= min(candles.x)


def test_equity_chart_thinning_keeps_the_worst_drawdown():
    result = run(20_000)
    figure = equity_chart(result)
    drawdown = next(t for t in figure.data if t.name == "Drawdown")

    assert len(drawdown.y) < len(result.equity_curve)
    assert min(drawdown.y) == pytest.approx(result.metrics["max_drawdown"] * 100)


def test_exposure_chart_reports_gross_net_and_position_count():
    figure = exposure_chart(run(400))
    assert {"Gross exposure", "Net exposure"} <= {t.name for t in figure.data}
