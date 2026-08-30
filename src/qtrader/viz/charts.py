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
from ..features.stock import macd

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


def _price_lines(indicators: pd.DataFrame, bars: pd.DataFrame) -> list[str]:
    """Indicator columns that genuinely live on the price axis.

    The name prefix alone is not enough: `vwap_side` starts with "vwap" but is a
    sign in {-1, 0, +1}. Drawn on the price axis it forces the range down to
    zero and flattens the candles into a band at the top of the panel. So the
    name proposes and the *values* decide — a line is only drawn against price
    if it actually sits inside the bar range.
    """
    low, high = bars["low"].min(), bars["high"].max()
    if not (np.isfinite(low) and np.isfinite(high)):
        return []
    margin = max((high - low) * 2.0, abs(high) * 0.1)

    keep = []
    for column in indicators.columns:
        if not column.startswith(PRICE_LINE_PREFIXES):
            continue
        values = indicators[column].dropna()
        if values.empty:
            continue
        if low - margin <= values.median() <= high + margin:
            keep.append(column)
    return keep


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
    result: BacktestResult, symbol: str, *, height: int = 940,
    max_candles: int = MAX_CANDLES, window: tuple | None = None
) -> go.Figure:
    """One symbol's candles, volume, MACD, the strategy's own view, and its fills.

    The layout is fixed so that any strategy produces a readable chart:

    * **price** — candles, any price-level indicator lines the strategy exposes,
      and a marker on every executed fill;
    * **volume**;
    * **MACD** — always present. If the strategy publishes its own MACD those
      series are drawn, since its periods are the ones that mattered; otherwise
      a standard MACD(12, 26, 9) is computed here as a reference and labelled
      as such, so a chart never lacks the indicator a reader expects;
    * **the strategy's own panel** — its score, trend statistic or signal, when
      it exposes one and it is not already the MACD.
    """
    bars = result.panel.bars(symbol)
    if window is not None:
        # An explicit [start, end] slice, for charting one session out of many.
        # Clipping the x axis instead would leave every other bar in the figure,
        # which is invisible on screen and very visible in the file size.
        start, end = window
        bars = bars.loc[(bars.index >= start) & (bars.index <= end)]
    truncated = window is None and len(bars) > max_candles
    if truncated:
        bars = bars.tail(max_candles)
    x = _market_naive(bars.index)

    indicators = result.signals.indicators.get(symbol, pd.DataFrame(index=bars.index))
    indicators = indicators.reindex(bars.index)

    price_lines = _price_lines(indicators, bars)
    macd_panel, macd_is_reference = _macd_panel(indicators, bars["close"])
    strategy_column = _strategy_panel_column(indicators)

    rows = 4 if strategy_column else 3
    heights = [0.48, 0.10, 0.21, 0.21] if strategy_column else [0.58, 0.12, 0.30]
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=heights
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

    _add_trade_markers(
        fig, result, symbol, row=1,
        since=bars.index[0] if (truncated or window is not None) else None,
        until=bars.index[-1] if window is not None else None,
    )

    fig.add_trace(
        go.Bar(x=x, y=bars["volume"], name="Volume", marker_color=FLAT_COLOR, showlegend=False),
        row=2,
        col=1,
    )

    _add_macd_panel(fig, x, macd_panel, row=3, reference=macd_is_reference)
    if strategy_column:
        _add_series_panel(fig, x, indicators[strategy_column], strategy_column, row=4)

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


def _macd_panel(indicators: pd.DataFrame, close: pd.Series) -> tuple[pd.DataFrame, bool]:
    """The strategy's MACD if it publishes one, otherwise a standard reference."""
    published = [c for c in ("macd", "macd_signal", "macd_hist") if c in indicators.columns]
    if published:
        return indicators[published], False
    return macd(close), True


def _strategy_panel_column(indicators: pd.DataFrame) -> str | None:
    """The one series that best represents what this strategy was watching."""
    for column in ("score", "trend_zscore", "signal"):
        if column in indicators.columns:
            return column
    return None


def _add_macd_panel(
    fig: go.Figure, x: pd.DatetimeIndex, panel: pd.DataFrame, *, row: int, reference: bool
) -> None:
    """Histogram bars plus whichever MACD lines are available."""
    if "macd_hist" in panel:
        colors = [LONG_COLOR if v >= 0 else SHORT_COLOR for v in panel["macd_hist"]]
        fig.add_trace(
            go.Bar(x=x, y=panel["macd_hist"], name="MACD hist",
                   marker_color=colors, showlegend=False),
            row=row,
            col=1,
        )
    for column, width in (("macd", 1.4), ("macd_signal", 1.2)):
        if column not in panel:
            continue
        fig.add_trace(
            go.Scatter(x=x, y=panel[column], mode="lines", name=column.upper(),
                       line=dict(width=width)),
            row=row,
            col=1,
        )
    label = "MACD (12,26,9 ref)" if reference else "MACD"
    fig.update_yaxes(title_text=label, row=row, col=1)


def _add_series_panel(
    fig: go.Figure, x: pd.DatetimeIndex, series: pd.Series, name: str, *, row: int
) -> None:
    """A single strategy series with a zero line for reference."""
    fig.add_trace(
        go.Scatter(x=x, y=series, mode="lines", name=name.upper(),
                   line=dict(width=1.3, color=ACCENT_COLOR)),
        row=row,
        col=1,
    )
    fig.add_hline(y=0, line=dict(width=1, color=FLAT_COLOR), row=row, col=1)
    fig.update_yaxes(title_text=name.replace("_", " ").title(), row=row, col=1)


def _add_trade_markers(
    fig: go.Figure, result: BacktestResult, symbol: str, *, row: int, since=None, until=None
) -> None:
    """Mark every fill in this symbol at its executed price: buys below, sells above."""
    fills = result.fills
    if fills.empty:
        return
    fills = fills.loc[fills["symbol"] == symbol]
    if since is not None:
        fills = fills.loc[fills["timestamp"] >= since]
    if until is not None:
        fills = fills.loc[fills["timestamp"] <= until]
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
