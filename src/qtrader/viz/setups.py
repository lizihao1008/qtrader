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
from .charts import (
    ACCENT_COLOR,
    FLAT_COLOR,
    LONG_COLOR,
    SHORT_COLOR,
    _intraday_rangebreaks,
    _market_naive,
)

#: Faint level families drawn for context, in draw order.
CONTEXT_LEVELS = (
    ("previous_day_resistance", "PDH"),
    ("previous_day_support", "PDL"),
    ("opening_range_resistance", "ORH"),
    ("opening_range_support", "ORL"),
)

KRONOS_COLOR = "#ab47bc"
#: The consolidation box. Deliberately not the S/R colour: one is a level the
#: strategy watches, the other is a regime a separate detector reports.
RANGE_COLOR = "#f9a825"
LEVEL_COLOR = "#455a64"


def setup_gallery(
    episodes: Episodes,
    episode_ids: list[str],
    *,
    title: str,
    forecasts: dict[tuple[pd.Timestamp, str], pd.DataFrame] | None = None,
    scores: pd.DataFrame | None = None,
    llm_actions: dict[str, dict] | None = None,
    ranges: dict[str, pd.DataFrame] | None = None,
    columns: int = 4,
    height_per_row: int = 300,
) -> go.Figure:
    """A grid of real-price panels: candles, the watched level, and the forecast.

    ``ranges`` is the consolidation state per symbol — the frame
    ``experiments.consolidation_gate.range_state`` returns, indexed by timestamp
    with ``in_range`` / ``range_id`` / ``range_high`` / ``range_low``. Omitted,
    the panels are exactly what they were.
    """
    forecasts = forecasts or {}
    rows = max(1, -(-len(episode_ids) // columns))

    figure = make_subplots(
        rows=rows,
        cols=columns,
        subplot_titles=[
            _panel_title(
                episodes, episode_id, scores,
                (llm_actions or {}).get(episode_id),
            )
            for episode_id in episode_ids
        ],
        # Plotly caps vertical spacing at 1/(rows-1).
        vertical_spacing=min(0.09, 0.85 / (rows - 1)) if rows > 1 else 0.09,
        horizontal_spacing=0.045,
    )

    for position, episode_id in enumerate(episode_ids):
        row, col = divmod(position, columns)
        _draw_panel(figure, episodes, episode_id, forecasts, row + 1, col + 1,
                    ranges=ranges)

    figure.update_layout(
        title=title,
        height=height_per_row * rows + 110,
        margin=dict(l=55, r=20, t=110, b=45),
        showlegend=False,
        plot_bgcolor="white",
    )
    # Real exchange-local time. Each panel spans its own hours, so the axes are
    # not shared -- the point of the grid is to compare shapes, and a common
    # time axis across unrelated sessions would compare nothing. Rangebreaks
    # keep an overnight gap from stretching a panel that straddles one.
    # Clock time on the axis, the date in the panel title. A window is usually
    # one session, and letting plotly pick its own format gives some panels
    # rotated date stamps and others plain times, which makes a grid hard to
    # read across.
    figure.update_xaxes(rangeslider_visible=False, showgrid=True, gridcolor="#eceff1",
                        tickfont=dict(size=9), tickformat="%H:%M",
                        rangebreaks=_intraday_rangebreaks())
    figure.update_yaxes(showgrid=True, gridcolor="#eceff1", tickfont=dict(size=9))
    # Axis titles only on the outer edge; repeating them on every panel spends
    # space that the panels themselves need.
    last_row = -(-len(episode_ids) // columns)
    for col in range(1, columns + 1):
        figure.update_xaxes(title_text="exchange-local time", title_font=dict(size=10),
                            row=last_row, col=col)
    for row in range(1, rows + 1):
        figure.update_yaxes(title_text="price", title_font=dict(size=10), row=row, col=1)
    for annotation in figure.layout.annotations:
        annotation.font.size = 10
    return figure


# --------------------------------------------------------------------- panel
def _draw_panel(figure, episodes, episode_id, forecasts, row, col, *, ranges=None):
    window = episodes.window(episode_id).reset_index()
    meta = episodes.features.loc[episode_id]
    long = meta["direction"] == "LONG"
    hold = int(meta["hold_bars"])
    when = _market_naive(window["timestamp"])

    figure.add_trace(
        go.Candlestick(
            x=when,
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

    # Drawn before the levels so the box sits behind the lines that matter.
    _draw_ranges(figure, window, meta, ranges, row, col)
    _draw_levels(figure, window, row, col)
    _draw_entry_and_exit(figure, window, meta, long, hold, row, col)
    _draw_peak(figure, window, long, hold, row, col)
    _draw_reductions(figure, window, row, col)
    _draw_forecast(figure, window, meta, forecasts, row, col)


def _draw_ranges(figure, window, meta, ranges, row, col):
    """Shade every consolidation box the detector was tracking in this window.

    One rectangle per ``range_id``, spanning the bars it was live for at the
    high/low it had frozen. Boxes are drawn from the detector's own state
    rather than recomputed, so the picture is what the gate actually saw —
    including a box that was still live when the trade opened.
    """
    if not ranges:
        return
    state = ranges.get(meta["symbol"])
    if state is None or state.empty:
        return
    stamps = pd.DatetimeIndex(window["timestamp"])
    live = state.reindex(stamps)
    live = live.loc[live["in_range"].fillna(False).to_numpy(dtype=bool)]
    if live.empty:
        return

    local = _market_naive(pd.Series(live.index))
    for range_id, group in pd.DataFrame({
        "at": local,
        "range_id": live["range_id"].to_numpy(),
        "high": live["range_high"].to_numpy(dtype=float),
        "low": live["range_low"].to_numpy(dtype=float),
    }).groupby("range_id"):
        high, low = float(group["high"].iloc[-1]), float(group["low"].iloc[-1])
        if not (np.isfinite(high) and np.isfinite(low)) or high <= low:
            continue
        figure.add_shape(
            type="rect", x0=group["at"].iloc[0], x1=group["at"].iloc[-1],
            y0=low, y1=high, fillcolor=RANGE_COLOR, opacity=0.13,
            line=dict(width=1, color=RANGE_COLOR, dash="dot"),
            layer="below", row=row, col=col,
        )
        figure.add_annotation(
            x=group["at"].iloc[0], y=high, text="震荡区间",
            showarrow=False, xanchor="left", yanchor="bottom",
            font=dict(size=8, color=RANGE_COLOR), row=row, col=col,
        )


def _draw_levels(figure, window, row, col):
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

    entry_at = _stamp(entry_row)
    exit_at = _stamp(exit_row)
    if entry_at is not None:
        figure.add_vline(x=entry_at, line=dict(width=1.1, color=LEVEL_COLOR),
                         row=row, col=col)
    if exit_at is not None:
        figure.add_vline(x=exit_at, line=dict(width=1, color=FLAT_COLOR, dash="dot"),
                         row=row, col=col)

    colour = LONG_COLOR if long else SHORT_COLOR
    if np.isfinite(entry_price) and entry_at is not None:
        figure.add_trace(
            go.Scatter(
                x=[entry_at], y=[entry_price], mode="markers",
                marker=dict(symbol="triangle-up" if long else "triangle-down",
                            size=12, color=colour,
                            line=dict(width=1, color="white")),
                showlegend=False, name="entry",
            ),
            row=row, col=col,
        )
    if np.isfinite(exit_price) and exit_at is not None:
        figure.add_trace(
            go.Scatter(
                x=[exit_at], y=[exit_price], mode="markers",
                marker=dict(symbol="x", size=8, color=LEVEL_COLOR),
                showlegend=False, name="exit",
            ),
            row=row, col=col,
        )


def _draw_reductions(figure, window, row, col):
    """Mark where the position was cut back without being closed.

    A scale-out is invisible in the entry/exit markers: the strategy leaves a
    smaller position running, and the engine records the slice as its own round
    trip, so the panel would show an ordinary exit. Reading the weight path
    directly is the only way to tell "took half off" from "closed".
    """
    if "target_weight" not in window:
        return
    weight = window["target_weight"].to_numpy(dtype=float)
    if not np.isfinite(weight).any():
        return

    previous = np.concatenate([[0.0], weight[:-1]])
    size, was = np.abs(weight), np.abs(previous)
    # smaller than the bar before, same side, and still holding something
    cut = (size > 1e-12) & (was > size + 1e-12) & (np.sign(weight) == np.sign(previous))
    if not cut.any():
        return

    at = np.flatnonzero(cut)
    when = _market_naive(window["timestamp"].to_numpy()[at])
    figure.add_trace(
        go.Scatter(
            x=when, y=window["close"].to_numpy()[at],
            mode="markers",
            marker=dict(symbol="triangle-down-open", size=11,
                        line=dict(width=2, color=ACCENT_COLOR)),
            showlegend=False, name="scaled out",
            customdata=[[f"{1 - size[i] / was[i]:.0%}"] for i in at],
            hovertemplate="took %{customdata[0]} off<extra></extra>",
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
            x=[_market_naive([held.loc[best, "timestamp"]])[0]], y=[series.loc[best]],
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
    # The forecast's own index is not relied on: it is set by whoever produced
    # it and a caller may hand back a plain range. The bars it predicts are the
    # window's own, counted forward from the decision bar, extrapolated with the
    # median spacing once the window runs out.
    stamps = _forecast_times(window, decision, len(path))
    figure.add_trace(
        go.Scatter(
            x=stamps,
            y=np.concatenate([[anchor], path]),
            mode="lines+markers",
            line=dict(width=1.8, color=KRONOS_COLOR, dash="dash"),
            marker=dict(size=3, color=KRONOS_COLOR),
            showlegend=False, name="Kronos",
        ),
        row=row, col=col,
    )


def _panel_title(episodes, episode_id, scores, llm_decision=None) -> str:
    meta = episodes.features.loc[episode_id]
    window = episodes.window(episode_id).reset_index()
    arrow = "▲ LONG" if meta["direction"] == "LONG" else "▼ SHORT"
    entry = _market_naive([meta["entry_time"]])[0]
    head = f"{meta['symbol']} {arrow} · {meta['gross_return_bps']:+.0f} bps"
    tail = f"{entry:%Y-%m-%d} · {int(meta['hold_bars'])} bars held"

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
    if llm_decision is not None:
        action = llm_decision.get("action", "unknown")
        label = {
            "keep": "KEEP ✓",
            "veto": "VETO ✕",
            "abstain": "ABSTAIN ○",
            "expired": "EXPIRED ⧖",
        }.get(action, action.upper())
        verdict = llm_decision.get("verdict") or {}
        confidence = verdict.get(
            "long_confidence" if meta["direction"] == "LONG" else "short_confidence"
        )
        tail += f" · LLM {label}"
        if confidence is not None:
            tail += f" {float(confidence):.2f}"
    # The readings the entry gate actually tested, at the decision bar. Without
    # them a panel shows what happened but not what the rule was looking at.
    gates = []
    for column, label, fmt in (
        ("momentum_z", "z", "{:+.2f}"),
        ("relative_volume", "rvol", "{:.2f}"),
        ("horizon_sigma_bps", "σH", "{:.0f}bps"),
        ("acceptance_count", "accept", "{:.0f}"),
    ):
        value = _at(decision, column)
        if np.isfinite(value):
            gates.append(f"{label} {fmt.format(value)}")
    side = _at(decision, "vwap_side")
    if np.isfinite(side) and side != 0:
        gates.append("vwap " + ("above" if side > 0 else "below"))
    if gates:
        tail += "<br>" + " · ".join(gates)
    # Three lines: a four-column grid cannot fit this on one without truncating.
    return f"{head}<br><span style='font-size:9px;color:#607d8b'>{tail}</span>"


def _forecast_times(window, decision, count: int):
    """Exchange-local x positions for the decision bar plus ``count`` bars after it."""
    timestamps = pd.DatetimeIndex(window["timestamp"])
    start = pd.Timestamp(decision["timestamp"].iloc[0])
    ahead = timestamps[timestamps > start][:count]

    if len(ahead) < count:
        step = pd.Series(timestamps).diff().median()
        last = ahead[-1] if len(ahead) else start
        extra = [last + step * (k + 1) for k in range(count - len(ahead))]
        ahead = ahead.append(pd.DatetimeIndex(extra))

    return _market_naive(pd.DatetimeIndex([start]).append(ahead))


def _stamp(row):
    """Exchange-local timestamp of a single window row, or None if it is absent."""
    if row.empty:
        return None
    return _market_naive([row["timestamp"].iloc[0]])[0]
