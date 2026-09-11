"""Plotly replay of a Kalman–CUSUM regime path.

Same visual contract as the rest of :mod:`qtrader.viz`: exchange-local time,
intraday range-breaks, candlesticks, the shared colour palette. The detector
is causal; this module only displays its output (and, separately, an
evaluation-only delay/FAR scatter).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..data.sessions import MARKET_TZ
from .charts import (
    ACCENT_COLOR,
    FLAT_COLOR,
    LONG_COLOR,
    MAX_CANDLES,
    SHORT_COLOR,
    _add_series_panel,
    _intraday_rangebreaks,
    _market_naive,
)

UP_FACE = LONG_COLOR
DOWN_FACE = SHORT_COLOR
FLAT_FACE = FLAT_COLOR
REVERSAL_COLOR = "#6a1b9a"

STATE_FACE = {"UP": UP_FACE, "DOWN": DOWN_FACE, "FLAT": FLAT_FACE}


def plot_regime(
    bars: pd.DataFrame,
    result: pd.DataFrame,
    *,
    config=None,
    title: str | None = None,
    path: str | Path | None = None,
    cusum_h: float | None = None,
    reversal_h: float | None = None,
    entry_z: float | None = None,
    height: int = 940,
    max_candles: int = MAX_CANDLES,
) -> go.Figure:
    """Candles with regime shading, volume, slope_z, and the two CUSUMs.

    Layout matches :func:`qtrader.viz.charts.price_chart`: price, volume, then
    the detector's own panels. Time is exchange-local with the same range
    breaks. ``path`` writes HTML (the plotly report convention); it never
    rasterises.

    Pass ``config`` (the :class:`~qtrader.regime.KalmanCUSUMConfig` the replay
    actually used) and the reference lines come from it. Hand-passing
    ``entry_z`` / ``cusum_h`` / ``reversal_h`` still works and still wins, but
    it is how a chart ends up drawing a threshold the detector never applied.
    """
    if config is not None:
        if entry_z is None:
            entry_z = config.entry_z
        if cusum_h is None:
            cusum_h = config.cusum_h
        if reversal_h is None:
            reversal_h = config.reversal_h
    bars, result, truncated = _align(bars, result, max_candles)
    x = _market_naive(bars.index)

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.48, 0.10, 0.21, 0.21],
    )
    _shade_states(fig, x, result["state"].astype(str).to_numpy(), row=1)
    fig.add_trace(
        go.Candlestick(
            x=x, open=bars["open"], high=bars["high"],
            low=bars["low"], close=bars["close"], name="OHLC",
            increasing_line_color=LONG_COLOR, decreasing_line_color=SHORT_COLOR,
        ),
        row=1, col=1,
    )
    if "smooth_close" in result.columns:
        fig.add_trace(
            go.Scatter(
                x=x, y=result["smooth_close"], mode="lines", name="kalman level",
                line=dict(width=1.4, color=ACCENT_COLOR),
            ),
            row=1, col=1,
        )
    _mark_transitions(fig, x, bars["close"].to_numpy(), result, row=1)
    fig.add_trace(
        go.Bar(x=x, y=bars["volume"], name="Volume",
               marker_color=FLAT_COLOR, showlegend=False),
        row=2, col=1,
    )
    _add_series_panel(fig, x, result["slope_z"], "slope_z (random-walk units)", row=3)
    if entry_z is not None:
        fig.add_hline(
            y=entry_z, line=dict(width=1, color=LONG_COLOR, dash="dash"),
            row=3, col=1,
        )
        fig.add_hline(
            y=-entry_z, line=dict(width=1, color=SHORT_COLOR, dash="dash"),
            row=3, col=1,
        )
    fig.add_hline(y=0, line=dict(width=1, color=FLAT_COLOR), row=4, col=1)
    fig.add_trace(
        go.Scatter(
            x=x, y=result["cusum_up"], mode="lines", name="CUSUM up",
            line=dict(width=1.3, color=LONG_COLOR),
        ),
        row=4, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=result["cusum_down"], mode="lines", name="CUSUM down",
            line=dict(width=1.3, color=SHORT_COLOR),
        ),
        row=4, col=1,
    )
    fig.update_yaxes(title_text="CUSUM", row=4, col=1)
    if cusum_h is not None:
        fig.add_hline(
            y=cusum_h, line=dict(width=1, color=FLAT_COLOR, dash="dash"),
            row=4, col=1,
        )
    if reversal_h is not None:
        fig.add_hline(
            y=reversal_h, line=dict(width=1, color=REVERSAL_COLOR, dash="dot"),
            row=4, col=1,
        )
    cap = max(v for v in (cusum_h, reversal_h, 0.0) if v is not None)
    if cap > 0:
        fig.update_yaxes(range=[0.0, cap * 2.2], row=4, col=1)

    window = f", last {len(bars):,} bars" if truncated else ""
    fig.update_layout(
        title=title or f"Kalman CUSUM ({MARKET_TZ}{window})",
        height=height,
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        margin=dict(l=60, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    fig.update_xaxes(rangebreaks=_intraday_rangebreaks())
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=4, col=1)
    _write_html(fig, path)
    return fig


def plot_delay_far(
    grid: pd.DataFrame,
    *,
    path: str | Path | None = None,
    title: str = "Detection delay vs false-alarm rate",
    label_cols: tuple[str, ...] = ("cusum_k", "cusum_h"),
    height: int = 560,
) -> go.Figure:
    """Scatter of mean delay against false alarms per session.

    Each point is one causal replay. The label is evaluation-only.
    """
    labels = [_cell_label(row, label_cols) for _, row in grid.iterrows()]
    fig = go.Figure(
        go.Scatter(
            x=grid["false_alarms_per_session"],
            y=grid["mean_delay"],
            mode="markers+text",
            text=labels,
            textposition="top right",
            textfont=dict(size=10, color="#455a64"),
            marker=dict(size=10, color=ACCENT_COLOR),
            name="grid",
            hovertemplate=(
                "FAR/session %{x:.2f}<br>delay %{y:.2f} bars"
                "<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        title=title,
        height=height,
        hovermode="closest",
        margin=dict(l=60, r=40, t=60, b=50),
        xaxis_title="false alarms per session",
        yaxis_title="mean detection delay (bars)",
        template="plotly_white",
    )
    _write_html(fig, path)
    return fig


def _align(
    bars: pd.DataFrame, result: pd.DataFrame, max_candles: int
) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    if "close" not in bars.columns:
        raise KeyError("bar frame must contain a 'close' column")
    if not bars.index.equals(result.index):
        result = result.reindex(bars.index)
    truncated = len(bars) > max_candles
    if truncated:
        bars = bars.tail(max_candles)
        result = result.tail(max_candles)
    if not {"open", "high", "low"}.issubset(bars.columns):
        bars = bars.copy()
        bars["open"] = bars["high"] = bars["low"] = bars["close"]
    if "volume" not in bars.columns:
        bars = bars.copy()
        bars["volume"] = 0.0
    return bars, result, truncated


def _shade_states(fig: go.Figure, x: pd.DatetimeIndex, states: np.ndarray, *, row: int) -> None:
    start = 0
    current = states[0]
    for i in range(1, len(states) + 1):
        if i == len(states) or states[i] != current:
            fig.add_vrect(
                x0=x[start], x1=x[i - 1],
                fillcolor=STATE_FACE.get(current, FLAT_FACE),
                opacity=0.16, line_width=0, layer="below",
                row=row, col=1,
            )
            if i < len(states):
                current = states[i]
                start = i


def _mark_transitions(
    fig: go.Figure, x: pd.DatetimeIndex, close: np.ndarray, result: pd.DataFrame, *, row: int
) -> None:
    states = result["state"].astype(str)
    prev = states.shift(1, fill_value="FLAT")
    specs = {
        ("FLAT", "UP"): ("triangle-up", LONG_COLOR, "FLAT→UP"),
        ("FLAT", "DOWN"): ("triangle-down", SHORT_COLOR, "FLAT→DOWN"),
        ("UP", "DOWN"): ("diamond", REVERSAL_COLOR, "UP→DOWN"),
        ("DOWN", "UP"): ("diamond", REVERSAL_COLOR, "DOWN→UP"),
    }
    buckets: dict[tuple[str, str], list[int]] = {key: [] for key in specs}
    for i, (was, now) in enumerate(zip(prev, states)):
        key = (was, now)
        if key in buckets:
            buckets[key].append(i)
    for key, idx in buckets.items():
        if not idx:
            continue
        marker, color, label = specs[key]
        fig.add_trace(
            go.Scatter(
                x=x.take(idx), y=close[idx], mode="markers", name=label,
                marker=dict(symbol=marker, size=11, color=color,
                            line=dict(width=1, color="#263238")),
            ),
            row=row, col=1,
        )


def _cell_label(row: pd.Series, cols: tuple[str, ...]) -> str:
    parts = []
    for col in cols:
        if col in row.index and pd.notna(row[col]):
            parts.append(f"{col.split('_')[-1]}={row[col]:g}")
    return " ".join(parts)


def _write_html(fig: go.Figure, path: str | Path | None) -> None:
    if path is None:
        return
    out = Path(path)
    if out.suffix.lower() != ".html":
        out = out.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(out, include_plotlyjs="cdn", full_html=True)
