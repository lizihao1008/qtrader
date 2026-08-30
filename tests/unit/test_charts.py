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


def test_a_strategy_exposing_only_a_histogram_still_charts():
    """Draw the MACD series the strategy has, not the ones it usually would."""
    result = run(200)
    result.signals.indicators["AAA"] = pd.DataFrame(
        {"macd_hist": range(200), "trend_zscore": range(200)},
        index=result.panel.index,
    )

    names = {trace.name for trace in price_chart(result, "AAA").data}
    assert "MACD hist" in names
    assert "MACD" not in names  # the strategy has no MACD line, so none is invented


def test_every_chart_has_a_macd_panel_even_without_a_macd_strategy():
    """The reader expects MACD; a strategy that ignores it should not remove it."""
    result = run(200)
    result.signals.indicators["AAA"] = pd.DataFrame(
        {"score": range(200)}, index=result.panel.index
    )

    figure = price_chart(result, "AAA")
    names = {trace.name for trace in figure.data}
    assert {"MACD", "MACD_SIGNAL", "MACD hist"} <= names
    axis_titles = {
        figure.layout[key].title.text
        for key in figure.layout
        if key.startswith("yaxis") and figure.layout[key].title.text
    }
    assert "MACD (12,26,9 ref)" in axis_titles  # labelled as not the strategy's own


def test_the_strategy_gets_its_own_panel_alongside_macd():
    result = run(200)
    result.signals.indicators["AAA"] = pd.DataFrame(
        {"trend_zscore": range(200)}, index=result.panel.index
    )

    figure = price_chart(result, "AAA")
    assert {"TREND_ZSCORE", "MACD"} <= {trace.name for trace in figure.data}


def test_the_chart_can_be_limited_to_a_recent_window(tmp_path):
    """A run may load warm-up history without the chart being buried in it."""
    from qtrader.viz.report import write_report

    result = run(1_500)
    path = write_report(result, tmp_path / "r.html", symbols=("AAA",), max_candles=300)
    assert "last 300 bars" in path.read_text()

    full = write_report(result, tmp_path / "full.html", symbols=("AAA",))
    assert "last" not in full.read_text().split("</title>")[0]


def test_a_gallery_of_every_losing_trade_renders():
    """Plotly caps subplot spacing by row count; a large gallery must still build."""
    from qtrader.analysis import extract_episodes
    from qtrader.analysis.episodes import Episodes
    from qtrader.viz.episodes import episode_gallery

    result = run(2_000)
    episodes = Episodes(
        features=pd.DataFrame(
            {
                "symbol": ["AAA"] * 40,
                "direction": ["LONG"] * 40,
                "gross_return_bps": range(40),
                "hold_bars": [5] * 40,
            },
            index=[f"e{i}" for i in range(40)],
        ),
        bars=pd.concat(
            [
                pd.DataFrame(
                    {
                        "episode_id": f"e{i}",
                        "offset": range(-3, 6),
                        "timestamp": result.panel.index[:9],
                        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                    }
                )
                for i in range(40)
            ],
            ignore_index=True,
        ),
    )
    figure = episode_gallery(episodes, list(episodes.features.index), title="all of them")
    assert len(figure.data) == 40


def test_a_sign_series_is_not_drawn_on_the_price_axis():
    """`vwap_side` is in {-1,0,1}; on the price axis it flattens the candles."""
    result = run(120)
    indicators = pd.DataFrame(
        {
            "vwap_side": [1.0, -1.0] * 60,          # a sign, despite the name
            "vwap_session": [100.0] * 120,          # an actual price level
        },
        index=result.panel.index,
    )
    result.signals.indicators["AAA"] = indicators

    names = {str(trace.name).lower() for trace in price_chart(result, "AAA").data}
    assert "vwap_session" in names, "a real price level must still be drawn"
    assert "vwap_side" not in names, "a sign series was drawn against price"


def test_a_window_charts_only_that_slice():
    result = run(300)
    index = result.panel.index
    figure = price_chart(result, "AAA", window=(index[100], index[140]))

    candles = next(t for t in figure.data if t.type == "candlestick")
    assert len(candles.x) == 41
    for trace in figure.data:
        if trace.name and trace.name.endswith("fill") and len(trace.x):
            assert min(trace.x) >= min(candles.x) and max(trace.x) <= max(candles.x)
