"""Backtest charts.

Three figures answer the three questions a researcher asks first:

1. :func:`price_chart` — *where did it trade?* One symbol's candlesticks with
   the strategy's own indicators and every fill marked on the bar it executed on.
2. :func:`equity_chart` — *what did that earn?* Cumulative return over time
   against the benchmark, with the drawdown underneath.
3. :func:`exposure_chart` — *how was the risk carried?* Gross and net exposure
   and the number of open positions, which is where a "market-neutral" strategy
   stops being neutral.

All of them plot in **exchange-local time** and use range breaks to hide nights
and weekends, so intraday bars appear continuous instead of being separated by
empty overnight gaps.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..backtest.engine import BacktestResult
from ..data.sessions import MARKET_TZ, RTH_CLOSE, RTH_OPEN

LONG_COLOR = "#26a69a"
SHORT_COLOR = "#ef5350"
FLAT_COLOR = "#90a4ae"
ACCENT_COLOR = "#5c6bc0"

#: Indicator columns drawn on the price axis; everything else goes to its own row.
PRICE_LINE_PREFIXES = ("sma", "ema", "vwap", "ma_", "band")

#: Candles drawn in a price chart. Beyond this the bars are thinner than a pixel
#: and the file grows without showing anything, so the most recent window is
#: charted and the title says so.
MAX_CANDLES = 2_000

#: Points drawn in a line chart. Longer runs are bucketed — last value for the
#: equity path, minimum for drawdown, so the worst point is never smoothed away.
MAX_LINE_POINTS = 4_000


def _market_naive(index: pd.DatetimeIndex | pd.Series) -> pd.DatetimeIndex:
    """UTC timestamps -> tz-naive exchange-local time, for a readable axis."""
    return pd.DatetimeIndex(index).tz_convert(MARKET_TZ).tz_localize(None)


def _intraday_rangebreaks() -> list[dict]:
    """Hide weekends and out-of-session hours on the x axis."""
    open_hour = RTH_OPEN.hour + RTH_OPEN.minute / 60
    close_hour = RTH_CLOSE.hour + RTH_CLOSE.minute / 60
    return [
        dict(bounds=["sat", "mon"]),
        dict(bounds=[close_hour, open_hour], pattern="hour"),
    ]


def price_chart(
    result: BacktestResult, symbol: str, *, height: int = 880, max_candles: int = MAX_CANDLES
) -> go.Figure:
    """Candlestick chart for one symbol with indicators, volume and its fills."""
    bars = result.panel.bars(symbol)
    truncated = len(bars) > max_candles
    if truncated:
        bars = bars.tail(max_candles)
    x = _market_naive(bars.index)
    indicators = result.signals.indicators.get(symbol, pd.DataFrame(index=bars.index))
    indicators = indicators.reindex(bars.index)

    price_lines = [c for c in indicators.columns if c.startswith(PRICE_LINE_PREFIXES)]
    lower_panel = _lower_panel_columns(indicators)

    rows = 3 if lower_panel else 2
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.62, 0.13, 0.25][:rows],
    )

    fig.add_trace(
        go.Candlestick(
            x=x,
            open=bars["open"],
            high=bars["high"],
            low=bars["low"],
            close=bars["close"],
            name=symbol,
            increasing_line_color=LONG_COLOR,
            decreasing_line_color=SHORT_COLOR,
        ),
        row=1,
        col=1,
    )
    for column in price_lines:
        fig.add_trace(
            go.Scatter(
                x=x, y=indicators[column], mode="lines", name=column.upper(),
                line=dict(width=1.4),
            ),
            row=1,
            col=1,
        )

    _add_trade_markers(fig, result, symbol, row=1, since=bars.index[0] if truncated else None)

    fig.add_trace(
        go.Bar(x=x, y=bars["volume"], name="Volume", marker_color=FLAT_COLOR, showlegend=False),
        row=2,
        col=1,
    )

    if lower_panel:
        _add_lower_panel(fig, x, indicators, lower_panel, row=3)

    window = f", last {len(bars):,} bars" if truncated else ""
    fig.update_layout(
        title=f"{symbol} — price and executed trades ({MARKET_TZ}{window})",
        height=height,
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    fig.update_xaxes(rangebreaks=_intraday_rangebreaks())
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=rows, col=1)
    return fig


def _lower_panel_columns(indicators: pd.DataFrame) -> list[str]:
    """Indicator columns that need their own axis below the price panel."""
    for group in (("macd", "macd_signal", "macd_hist"), ("score",), ("signal",)):
        present = [c for c in group if c in indicators.columns]
        if present:
            return present
    return []


def _add_lower_panel(
    fig: go.Figure, x: pd.DatetimeIndex, indicators: pd.DataFrame, columns: list[str], *, row: int
) -> None:
    """Draw MACD (bars + lines) or a single score series in the bottom panel."""
    if "macd_hist" in columns:
        colors = [LONG_COLOR if v >= 0 else SHORT_COLOR for v in indicators["macd_hist"]]
        fig.add_trace(
            go.Bar(x=x, y=indicators["macd_hist"], name="MACD hist",
                   marker_color=colors, showlegend=False),
            row=row,
            col=1,
        )
        for column in ("macd", "macd_signal"):
            fig.add_trace(
                go.Scatter(x=x, y=indicators[column], mode="lines", name=column.upper(),
                           line=dict(width=1.3)),
                row=row,
                col=1,
            )
        fig.update_yaxes(title_text="MACD", row=row, col=1)
        return

    column = columns[0]
    fig.add_trace(
        go.Scatter(x=x, y=indicators[column], mode="lines", name=column.upper(),
                   line=dict(width=1.3, color=ACCENT_COLOR)),
        row=row,
        col=1,
    )
    fig.add_hline(y=0, line=dict(width=1, color=FLAT_COLOR), row=row, col=1)
    fig.update_yaxes(title_text=column.replace("_", " ").title(), row=row, col=1)


def _add_trade_markers(
    fig: go.Figure, result: BacktestResult, symbol: str, *, row: int, since=None
) -> None:
    """Mark every fill in this symbol at its executed price: buys below, sells above."""
    fills = result.fills
    if fills.empty:
        return
    fills = fills.loc[fills["symbol"] == symbol]
    if since is not None:
        fills = fills.loc[fills["timestamp"] >= since]
    if fills.empty:
        return

    for side, marker, color, offset in (
        ("BUY", "triangle-up", LONG_COLOR, 0.998),
        ("SELL", "triangle-down", SHORT_COLOR, 1.002),
    ):
        subset = fills.loc[fills["side"] == side]
        if subset.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=_market_naive(subset["timestamp"]),
                y=subset["price"] * offset,
                mode="markers",
                name=f"{side} fill",
                marker=dict(symbol=marker, size=11, color=color,
                            line=dict(width=1, color="#263238")),
                customdata=subset[["shares", "price", "commission"]].to_numpy(),
                hovertemplate=(
                    f"<b>{side}</b> %{{customdata[0]:.0f}} sh<br>"
                    "fill %{customdata[1]:.4f}<br>fee %{customdata[2]:.2f}<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )


def _thin(curve: pd.DataFrame, columns: dict[str, str], limit: int) -> pd.DataFrame:
    """Bucket a long curve down to ``limit`` points using per-column aggregations."""
    if len(curve) <= limit:
        return curve
    bucket = np.arange(len(curve)) // int(np.ceil(len(curve) / limit))
    thinned = curve.groupby(bucket).agg(columns)
    thinned.index = curve.index[curve.groupby(bucket).size().cumsum() - 1]
    return thinned


def equity_chart(result: BacktestResult, *, height: int = 620) -> go.Figure:
    """Cumulative return vs the benchmark, with the drawdown profile below."""
    curve = _thin(
        result.equity_curve,
        {"cum_return": "last", "benchmark_cum_return": "last", "drawdown": "min"},
        MAX_LINE_POINTS,
    )
    x = _market_naive(curve.index)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.68, 0.32]
    )
    fig.add_trace(
        go.Scatter(x=x, y=curve["cum_return"] * 100, mode="lines",
                   name="Strategy (net of costs)", line=dict(width=2, color=LONG_COLOR)),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=x, y=curve["benchmark_cum_return"] * 100, mode="lines",
                   name=f"{result.benchmark} buy & hold (frictionless)",
                   line=dict(width=1.4, color=FLAT_COLOR, dash="dot")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=x, y=curve["drawdown"] * 100, mode="lines", name="Drawdown",
                   fill="tozeroy", line=dict(width=1, color=SHORT_COLOR)),
        row=2,
        col=1,
    )

    fig.update_layout(
        title="Cumulative return over time",
        height=height,
        hovermode="x unified",
        margin=dict(l=60, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    fig.update_xaxes(rangebreaks=_intraday_rangebreaks())
    fig.update_yaxes(title_text="Cumulative return (%)", row=1, col=1)
    fig.update_yaxes(title_text="Drawdown (%)", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)
    return fig


def exposure_chart(result: BacktestResult, *, height: int = 480) -> go.Figure:
    """Gross/net exposure as a share of equity, and the position count."""
    curve = _thin(
        result.equity_curve,
        {
            "gross_exposure": "last", "net_exposure": "last",
            "equity": "last", "n_positions": "max",
        },
        MAX_LINE_POINTS,
    )
    x = _market_naive(curve.index)
    gross = curve["gross_exposure"] / curve["equity"] * 100
    net = curve["net_exposure"] / curve["equity"] * 100

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.62, 0.38]
    )
    fig.add_trace(
        go.Scatter(x=x, y=gross, mode="lines", name="Gross exposure",
                   line=dict(width=1.4, color=ACCENT_COLOR)),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=x, y=net, mode="lines", name="Net exposure",
                   line=dict(width=1.4, color=SHORT_COLOR)),
        row=1,
        col=1,
    )
    fig.add_hline(y=0, line=dict(width=1, color=FLAT_COLOR), row=1, col=1)
    fig.add_trace(
        go.Scatter(x=x, y=curve["n_positions"], mode="lines", name="Open positions",
                   line=dict(width=1.2, color=FLAT_COLOR), showlegend=False),
        row=2,
        col=1,
    )

    fig.update_layout(
        title="Exposure over time",
        height=height,
        hovermode="x unified",
        margin=dict(l=60, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    fig.update_xaxes(rangebreaks=_intraday_rangebreaks())
    fig.update_yaxes(title_text="% of equity", row=1, col=1)
    fig.update_yaxes(title_text="Positions", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)
    return fig
