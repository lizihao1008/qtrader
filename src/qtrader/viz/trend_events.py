"""Candlestick audit charts for trend-start events.

The label used a future window. The chart shows that window after a vertical
line at the start — it does not move the start using anything that line hides.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..data.sessions import MARKET_TZ, session_date, to_market_time
from .charts import FLAT_COLOR, LONG_COLOR, SHORT_COLOR, _intraday_rangebreaks, _market_naive
from .report import REPORT_CSS


def trend_event_chart(
    bars: pd.DataFrame,
    event: pd.Series,
    *,
    pre_window: int = 40,
    post_extra: int = 10,
    height: int = 560,
) -> go.Figure:
    """One event: candles, volume, start line, t+H line, pre/post shading.

    The label uses ``(close_t, close_{t+H}]``. The start line is the *end* of
    bar ``t`` so that candle stays in the grey pre-window.
    """
    anchor = pd.Timestamp(event["bar_open"])
    start = pd.Timestamp(event["trend_start"])
    horizon = int(event["horizon"])
    end = start + pd.Timedelta(minutes=horizon)
    window = _window(bars, anchor, pre_window, horizon + post_extra + 1)
    if window.empty:
        raise ValueError(f"no bars around {event['symbol']} {start}")

    x = _market_naive(window.index)
    start_x = _market_naive(pd.DatetimeIndex([start]))[0]
    end_x = _market_naive(pd.DatetimeIndex([end]))[0]
    pre_x = x[0]
    post_x = x[-1]
    up = int(event["direction"]) > 0
    trend_color = LONG_COLOR if up else SHORT_COLOR
    side = "UP" if up else "DOWN"

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04,
        row_heights=[0.78, 0.22],
    )
    fig.add_vrect(
        x0=pre_x, x1=start_x, fillcolor="#90a4ae", opacity=0.12,
        line_width=0, row=1, col=1,
    )
    fig.add_vrect(
        x0=start_x, x1=end_x, fillcolor=trend_color, opacity=0.14,
        line_width=0, row=1, col=1,
    )
    fig.add_vrect(
        x0=end_x, x1=post_x, fillcolor="#fff59d", opacity=0.12,
        line_width=0, row=1, col=1,
    )
    fig.add_trace(
        go.Candlestick(
            x=x, open=window["open"], high=window["high"],
            low=window["low"], close=window["close"], name=event["symbol"],
            increasing_line_color=LONG_COLOR, decreasing_line_color=SHORT_COLOR,
            showlegend=False,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(x=x, y=window["volume"], name="Volume",
               marker_color=FLAT_COLOR, showlegend=False),
        row=2, col=1,
    )
    for stamp in (start_x, end_x):
        fig.add_vline(
            x=stamp, line=dict(width=1.6, dash="dash", color="#263238"),
            row=1, col=1,
        )
    y_top = float(window["high"].max())
    fig.add_annotation(
        x=start_x, y=y_top, text="start", showarrow=False,
        yanchor="bottom", xanchor="left", font=dict(size=11, color="#263238"),
        bgcolor="rgba(255,255,255,0.8)", row=1, col=1,
    )
    fig.add_annotation(
        x=end_x, y=y_top, text="t+H", showarrow=False,
        yanchor="bottom", xanchor="left", font=dict(size=11, color="#263238"),
        bgcolor="rgba(255,255,255,0.8)", row=1, col=1,
    )

    local = to_market_time(pd.DatetimeIndex([anchor]))[0]
    ret_pct = float(event["future_return"]) * 100
    fig.update_layout(
        title=dict(
            text=(
                f"{event['symbol']} | {local:%Y-%m-%d %H:%M} {MARKET_TZ}<br>"
                f"{side} TREND   Z={float(event['ztrend']):.2f} | "
                f"ER={float(event['ER']):.2f} | "
                f"MAE={float(event['MAE_norm']):.2f} | "
                f"MFE={float(event['MFE_norm']):.2f} | "
                f"Future{horizon}m={ret_pct:+.2f}%"
            ),
            x=0.01,
            xanchor="left",
            y=0.98,
            pad=dict(t=8, b=8),
        ),
        height=height,
        hovermode="x unified",
        showlegend=False,
        xaxis_rangeslider_visible=False,
        margin=dict(l=56, r=24, t=96, b=40),
    )
    fig.update_xaxes(rangebreaks=_intraday_rangebreaks())
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)
    return fig


def save_event_chart(
    bars: pd.DataFrame,
    event: pd.Series,
    folder: Path,
    *,
    pre_window: int = 40,
    post_extra: int = 10,
) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fig = trend_event_chart(bars, event, pre_window=pre_window, post_extra=post_extra)
    path = folder / _event_filename(event)
    fig.write_html(path, include_plotlyjs="cdn", full_html=True)
    return path


def write_trend_overview(
    path: Path,
    bars: dict[str, pd.DataFrame],
    up_events: pd.DataFrame,
    down_events: pd.DataFrame,
    *,
    pre_window: int = 40,
    post_extra: int = 10,
    columns: int = 3,
) -> Path:
    """One HTML file: a random UP grid and a random DOWN grid for flipping."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    up_html = _grid_html(bars, up_events, "Random UP trends", pre_window, post_extra, columns, js=True)
    down_html = _grid_html(bars, down_events, "Random DOWN trends", pre_window, post_extra, columns, js=False)
    path.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Trend overview</title>
<style>{REPORT_CSS}</style></head>
<body>
<h1>Trend start events — visual audit</h1>
<p class="subtitle">Labels look into the next H minutes. Grey = before start,
coloured = labelled window, yellow = extra observation. Not a trading signal.</p>
{up_html}
{down_html}
</body></html>
"""
    )
    return path


def _grid_html(
    bars: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    heading: str,
    pre_window: int,
    post_extra: int,
    columns: int,
    *,
    js: bool,
) -> str:
    if events.empty:
        return f"<h2>{heading}</h2><p class='note'>No events in this sample.</p>"
    chunks = [f"<h2>{heading} (n={len(events)})</h2>"]
    for i, (_, event) in enumerate(events.iterrows()):
        fig = trend_event_chart(
            bars[event["symbol"]], event,
            pre_window=pre_window, post_extra=post_extra, height=480,
        )
        chunks.append(
            fig.to_html(full_html=False, include_plotlyjs=("cdn" if js and i == 0 else False))
        )
    return "\n".join(chunks)


def _window(bars: pd.DataFrame, start: pd.Timestamp, pre: int, post: int) -> pd.DataFrame:
    """Same-session slice around ``start``. Do not pull the next open onto the axis."""
    loc = bars.index.get_indexer([start], method="nearest")[0]
    left = max(loc - pre, 0)
    right = min(loc + post + 1, len(bars))
    chunk = bars.iloc[left:right]
    day = session_date(pd.DatetimeIndex([bars.index[loc]])).iloc[0]
    return chunk.loc[session_date(chunk.index) == day]


def _event_filename(event: pd.Series) -> str:
    local = to_market_time(pd.DatetimeIndex([pd.Timestamp(event["bar_open"])]))[0]
    side = "UP" if int(event["direction"]) > 0 else "DOWN"
    return f"{event['symbol']}_{local:%Y%m%d_%H%M}_{side}.html"
