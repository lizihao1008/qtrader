"""Setup gallery: the S/R decision and the Kronos forecast, on the same panel.

`episodes.episode_gallery` normalises every panel to basis points from entry and
flips shorts so up is always profit. That is the right view for comparing the
*shape* of many trades. It is the wrong view here, because a support level is a
price, and a price does not survive being rescaled per panel.

So these panels stay in **real prices**, and each one carries the three things
the request is about:

* **the level the rule was watching** — solid line, with its tolerance zone
  shaded. This is `watched_level`, recorded inside the strategy's bar loop at
  the moment the entry was decided, not redrawn afterwards from the finished
  chart. The other level families in view are drawn faint and dashed.
* **the direction the rule predicted** — the entry marker points the way the
  strategy expected price to go, and the panel is titled with it.
* **what Kronos forecast** — the model's predicted close path, drawn forward
  from the entry bar over exactly the horizon it was asked for. Everything to
  the right of the entry line is prediction; everything to the left is what the
  model was shown.

Winners and losers are drawn identically and on the same axes, because the
comparison only means something if nothing but the data differs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis.episodes import Episodes
from .charts import ACCENT_COLOR, FLAT_COLOR, LONG_COLOR, SHORT_COLOR

#: Faint level families drawn for context, in draw order.
CONTEXT_LEVELS = (
    ("previous_day_resistance", "PDH"),
    ("previous_day_support", "PDL"),
    ("opening_range_resistance", "ORH"),
    ("opening_range_support", "ORL"),
)

KRONOS_COLOR = "#ab47bc"
LEVEL_COLOR = "#455a64"


def setup_gallery(
    episodes: Episodes,
    episode_ids: list[str],
    *,
    title: str,
    forecasts: dict[tuple[pd.Timestamp, str], pd.DataFrame] | None = None,
    scores: pd.DataFrame | None = None,
    columns: int = 4,
    height_per_row: int = 300,
) -> go.Figure:
    """A grid of real-price panels: candles, the watched level, and the forecast."""
    forecasts = forecasts or {}
    rows = max(1, -(-len(episode_ids) // columns))

    figure = make_subplots(
        rows=rows,
        cols=columns,
        subplot_titles=[
            _panel_title(episodes, episode_id, scores) for episode_id in episode_ids
        ],
        # Plotly caps vertical spacing at 1/(rows-1).
        vertical_spacing=min(0.09, 0.85 / (rows - 1)) if rows > 1 else 0.09,
        horizontal_spacing=0.045,
    )

    for position, episode_id in enumerate(episode_ids):
        row, col = divmod(position, columns)
        _draw_panel(figure, episodes, episode_id, forecasts, row + 1, col + 1)

    figure.update_layout(
        title=title,
        height=height_per_row * rows + 110,
        margin=dict(l=55, r=20, t=110, b=45),
        showlegend=False,
        plot_bgcolor="white",
    )
    figure.update_xaxes(rangeslider_visible=False, showgrid=True, gridcolor="#eceff1",
                        tickfont=dict(size=9))
    figure.update_yaxes(showgrid=True, gridcolor="#eceff1", tickfont=dict(size=9))
    # Axis titles only on the outer edge; repeating them on every panel spends
    # space that the panels themselves need.
    last_row = -(-len(episode_ids) // columns)
    for col in range(1, columns + 1):
        figure.update_xaxes(title_text="bars from entry", title_font=dict(size=10),
                            row=last_row, col=col)
    for row in range(1, rows + 1):
        figure.update_yaxes(title_text="price", title_font=dict(size=10), row=row, col=1)
    for annotation in figure.layout.annotations:
        annotation.font.size = 10
    return figure


# --------------------------------------------------------------------- panel
def _draw_panel(figure, episodes, episode_id, forecasts, row, col):
    window = episodes.window(episode_id).reset_index()
    meta = episodes.features.loc[episode_id]
    long = meta["direction"] == "LONG"
    hold = int(meta["hold_bars"])
    offset = window["offset"].to_numpy()

    figure.add_trace(
        go.Candlestick(
            x=offset,
            open=window["open"], high=window["high"],
            low=window["low"], close=window["close"],
            increasing_line_color=LONG_COLOR,
            decreasing_line_color=SHORT_COLOR,
            increasing_line_width=1,
            decreasing_line_width=1,
            showlegend=False,
            name=episode_id,
        ),
        row=row, col=col,
    )

    _draw_levels(figure, window, offset, row, col)
    _draw_entry_and_exit(figure, window, meta, long, hold, row, col)
    _draw_peak(figure, window, long, hold, row, col)
    _draw_forecast(figure, window, meta, forecasts, row, col)


def _draw_levels(figure, window, offset, row, col):
    """The watched level and its zone, plus the other families for context.

    Read at offset -1, the **decision** bar. At offset 0 the position is already
    open and the break state has been consumed, so the level is gone — reading
    it there would silently draw nothing on every panel.
    """
    decision = window.loc[window["offset"] == -1]
    # Only a level-based strategy publishes these. Everything else still gets
    # candles, markers, the peak and the forecast — the panel degrades, it does
    # not fail.
    level = _at(decision, "watched_level")
    zone = _at(decision, "zone")

    if np.isfinite(level):
        if np.isfinite(zone):
            # The break tolerance: touching a level by a cent is not a break.
            figure.add_hrect(
                y0=level - zone, y1=level + zone,
                fillcolor=LEVEL_COLOR, opacity=0.10, line_width=0,
                row=row, col=col,
            )
        figure.add_hline(
            y=level, line=dict(width=1.6, color=LEVEL_COLOR),
            annotation_text=f"S/R {level:g}",
            annotation_position="top left",
            annotation_font=dict(size=9, color=LEVEL_COLOR),
            row=row, col=col,
        )

    low, high = window["low"].min(), window["high"].max()
    for column, label in CONTEXT_LEVELS:
        if column not in window:
            continue
        series = window[column].dropna()
        if series.empty:
            continue
        value = float(series.iloc[-1])
        # Only draw what is actually in frame; an off-scale line rescales the
        # panel and hides the price action it was meant to contextualise.
        if not (low <= value <= high) or (np.isfinite(value) and abs(value - low) < 1e-12):
            continue
        figure.add_hline(
            y=value, line=dict(width=0.9, color=FLAT_COLOR, dash="dot"),
            annotation_text=label, annotation_position="bottom right",
            annotation_font=dict(size=8, color=FLAT_COLOR),
            row=row, col=col,
        )


def _at(row: pd.DataFrame, column: str) -> float:
    """One value from the decision bar, or NaN if the strategy has no such column."""
    if row.empty or column not in row:
        return float("nan")
    value = row[column].iloc[0]
    return float(value) if pd.notna(value) else float("nan")


def _draw_entry_and_exit(figure, window, meta, long, hold, row, col):
    """The entry marker points the way the rule predicted price would go."""
    entry_row = window.loc[window["offset"] == 0]
    entry_price = float(entry_row["open"].iloc[0]) if len(entry_row) else np.nan
    exit_row = window.loc[window["offset"] == hold]
    exit_price = float(exit_row["close"].iloc[0]) if len(exit_row) else np.nan

    figure.add_vline(x=0, line=dict(width=1.1, color=LEVEL_COLOR), row=row, col=col)
    figure.add_vline(x=hold, line=dict(width=1, color=FLAT_COLOR, dash="dot"),
                     row=row, col=col)

    colour = LONG_COLOR if long else SHORT_COLOR
    if np.isfinite(entry_price):
        figure.add_trace(
            go.Scatter(
                x=[0], y=[entry_price], mode="markers",
                marker=dict(symbol="triangle-up" if long else "triangle-down",
                            size=12, color=colour,
                            line=dict(width=1, color="white")),
                showlegend=False, name="entry",
            ),
            row=row, col=col,
        )
    if np.isfinite(exit_price):
        figure.add_trace(
            go.Scatter(
                x=[hold], y=[exit_price], mode="markers",
                marker=dict(symbol="x", size=8, color=LEVEL_COLOR),
                showlegend=False, name="exit",
            ),
            row=row, col=col,
        )


def _draw_peak(figure, window, long, hold, row, col):
    """The best the trade ever looked, between the entry and the exit.

    Drawn because the distance between this and the exit marker is the part of
    the move the exit gave back — the first thing to check when a strategy with
    a plausible entry still loses.
    """
    held = window.loc[(window["offset"] >= 0) & (window["offset"] <= hold)]
    if held.empty:
        return
    series = held["high"] if long else held["low"]
    if not np.isfinite(series).any():
        return
    best = series.idxmax() if long else series.idxmin()

    figure.add_trace(
        go.Scatter(
            x=[held.loc[best, "offset"]], y=[series.loc[best]],
            mode="markers",
            marker=dict(symbol="circle-open", size=9, line=dict(width=1.6, color=ACCENT_COLOR)),
            showlegend=False, name="peak",
        ),
        row=row, col=col,
    )


def _draw_forecast(figure, window, meta, forecasts, row, col):
    """Kronos's predicted close path, anchored at the decision bar's close."""
    # An episode's entry_time is the *fill*; the model was run on the decision
    # bar, one execution lag earlier. Reading that timestamp off the window
    # itself is exact and needs no knowledge of the lag setting.
    decision = window.loc[window["offset"] == -1]
    if decision.empty:
        return
    forecast = forecasts.get((pd.Timestamp(decision["timestamp"].iloc[0]), meta["symbol"]))
    if forecast is None or forecast.empty:
        return

    # Anchoring the drawn path at the decision bar's close keeps the picture
    # honest about what the model had actually seen.
    anchor = float(decision["close"].iloc[0])
    if not np.isfinite(anchor):
        return

    path = forecast["close"].to_numpy(dtype=float)
    figure.add_trace(
        go.Scatter(
            x=np.arange(-1, len(path)),
            y=np.concatenate([[anchor], path]),
            mode="lines+markers",
            line=dict(width=1.8, color=KRONOS_COLOR, dash="dash"),
            marker=dict(size=3, color=KRONOS_COLOR),
            showlegend=False, name="Kronos",
        ),
        row=row, col=col,
    )


def _panel_title(episodes, episode_id, scores) -> str:
    meta = episodes.features.loc[episode_id]
    window = episodes.window(episode_id).reset_index()
    arrow = "▲ LONG" if meta["direction"] == "LONG" else "▼ SHORT"
    head = f"{meta['symbol']} {arrow} · {meta['gross_return_bps']:+.0f} bps"
    tail = f"{int(meta['hold_bars'])} bars held"

    decision = window.loc[window["offset"] == -1]
    if scores is not None and not decision.empty:
        try:
            score = scores.at[
                pd.Timestamp(decision["timestamp"].iloc[0]), meta["symbol"]
            ]
        except KeyError:
            score = np.nan
        if np.isfinite(score):
            agrees = np.sign(score) == (1 if meta["direction"] == "LONG" else -1)
            tail += f" · Kronos {score:+.2f} {'✓' if agrees else '✗'}"
    # Two lines: a four-column grid cannot fit this on one without truncating.
    return f"{head}<br><span style='font-size:9px;color:#607d8b'>{tail}</span>"
