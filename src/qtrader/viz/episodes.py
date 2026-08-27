"""Episode gallery: what a working setup and a failing setup actually look like.

Condition tables say *which* variable separated wins from losses. They do not
show the shape of the thing, and shape is what a person recognises. The gallery
puts the best and worst trades side by side as small candlestick panels.

Two normalisations make the panels comparable, and both matter:

* the price axis is **basis points from the entry price**, so a $600 stock and a
  $30 one are on the same scale;
* the time axis is **bars relative to the entry**, so every entry sits at 0 and
  the setups line up.

Short episodes are flipped (with high and low exchanged, so the candles stay
valid) — up is profit in every panel. Without that, half the winners look like
falling charts and the eye learns nothing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis.episodes import Episodes, excursion_summary, oriented_paths
from ..runner import Run
from .charts import LONG_COLOR, SHORT_COLOR
from .report import REPORT_CSS

BPS = 1e4

#: Features whose quantile profile is printed in full under the ranking table.
PROFILED_FEATURES = 3


def _oriented(window: pd.DataFrame, direction: str, entry_price: float) -> pd.DataFrame:
    """Prices as bps from entry, flipped for shorts so up always means profit."""
    sign = 1.0 if direction == "LONG" else -1.0
    scaled = (window[["open", "high", "low", "close"]] / entry_price - 1.0) * BPS * sign
    if sign < 0:
        scaled = scaled.rename(columns={"high": "low", "low": "high"})
    return scaled


def episode_gallery(
    episodes: Episodes,
    episode_ids: list[str],
    *,
    title: str,
    columns: int = 3,
    height_per_row: int = 260,
) -> go.Figure:
    """A grid of normalised candlestick panels, one per episode."""
    rows = max(1, -(-len(episode_ids) // columns))
    features = episodes.features

    subplot_titles = []
    for episode_id in episode_ids:
        row = features.loc[episode_id]
        subplot_titles.append(
            f"{row['symbol']} {row['direction']} · "
            f"{row['gross_return_bps']:+.0f} bps · {int(row['hold_bars'])} bars"
        )

    # Plotly caps vertical spacing at 1/(rows-1), so a gallery of every losing
    # trade — which is the point of asking for one — has to tighten it.
    spacing = min(0.10, 0.9 / (rows - 1)) if rows > 1 else 0.10
    figure = make_subplots(
        rows=rows,
        cols=columns,
        subplot_titles=subplot_titles,
        vertical_spacing=spacing,
        horizontal_spacing=0.05,
    )

    for position, episode_id in enumerate(episode_ids):
        row, col = divmod(position, columns)
        window = episodes.window(episode_id)
        meta = features.loc[episode_id]
        entry_price = _entry_price(window)
        scaled = _oriented(window, meta["direction"], entry_price)

        figure.add_trace(
            go.Candlestick(
                x=window["offset"],
                open=scaled["open"],
                high=scaled["high"],
                low=scaled["low"],
                close=scaled["close"],
                increasing_line_color=LONG_COLOR,
                decreasing_line_color=SHORT_COLOR,
                showlegend=False,
                name=episode_id,
            ),
            row=row + 1,
            col=col + 1,
        )
        # Entry at 0, exit where the position was closed.
        figure.add_vline(x=0, line=dict(width=1.2, color="#455a64"), row=row + 1, col=col + 1)
        figure.add_vline(
            x=int(meta["hold_bars"]),
            line=dict(width=1, color="#90a4ae", dash="dot"),
            row=row + 1,
            col=col + 1,
        )
        figure.add_hline(x0=0, x1=1, y=0, line=dict(width=1, color="#cfd8dc"),
                         row=row + 1, col=col + 1)

    figure.update_layout(
        title=title,
        height=height_per_row * rows + 90,
        margin=dict(l=50, r=20, t=90, b=40),
        showlegend=False,
    )
    figure.update_xaxes(rangeslider_visible=False, title_text="bars from entry")
    figure.update_yaxes(title_text="bps from entry")
    for annotation in figure.layout.annotations:
        annotation.font.size = 11
    return figure


def path_chart(
    episodes: Episodes, *, height: int = 560, min_share: float = 0.2
) -> go.Figure:
    """Mean price path of winners and losers, aligned on the entry bar.

    The half to the left of zero is the part worth staring at: it is the setup
    the strategy claimed to recognise. If winners and losers look identical
    before the entry, the signal is not separating them, and no amount of exit
    tuning will fix that.

    The x range stops once fewer than ``min_share`` of episodes are still open.
    Past that point the "mean path" is a handful of unusually long trades, and
    those are exactly the ones that stayed open *because* they were losing — a
    mean taken there measures the survivorship, not the strategy. The lower
    panel shows how many episodes each point is averaging over.
    """
    paths = oriented_paths(episodes)
    counts = paths.groupby("offset").size()
    floor = max(int(len(episodes) * min_share), 1)
    last_offset = int(counts.loc[counts.index >= 0].ge(floor).pipe(
        lambda s: s.index[s].max() if s.any() else 0
    ))
    shown = paths.loc[paths["offset"] <= last_offset]

    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.72, 0.28]
    )
    for label, mask, color, dash in (
        ("Winners", shown["gross_win"], LONG_COLOR, "solid"),
        ("Losers", ~shown["gross_win"], SHORT_COLOR, "solid"),
        ("All episodes", pd.Series(True, index=shown.index), "#455a64", "dot"),
    ):
        mean_path = shown.loc[mask].groupby("offset")["path_bps"].mean()
        figure.add_trace(
            go.Scatter(
                x=mean_path.index,
                y=mean_path.to_numpy(),
                mode="lines",
                name=label,
                line=dict(width=1.6 if dash == "dot" else 2, color=color, dash=dash),
            ),
            row=1,
            col=1,
        )

    figure.add_trace(
        go.Scatter(
            x=counts.loc[:last_offset].index,
            y=counts.loc[:last_offset].to_numpy(),
            mode="lines",
            name="episodes open",
            line=dict(width=1.2, color="#90a4ae"),
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    for row in (1, 2):
        figure.add_vline(x=0, line=dict(width=1.2, color="#455a64"), row=row, col=1)
    figure.add_hline(y=0, line=dict(width=1, color="#cfd8dc"), row=1, col=1)

    figure.update_layout(
        title="Mean episode path (bps from entry, oriented so up is profit)",
        height=height,
        hovermode="x unified",
        margin=dict(l=60, r=40, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
    )
    figure.update_yaxes(title_text="bps from entry", row=1, col=1)
    figure.update_yaxes(title_text="episodes open", row=2, col=1)
    figure.update_xaxes(title_text="bars from entry", row=2, col=1)
    return figure


def _entry_price(window: pd.DataFrame) -> float:
    """The open of the entry bar — the reference every panel is scaled against."""
    entry = window.loc[window["offset"] == 0]
    return float(entry["open"].iloc[0]) if len(entry) else float(window["close"].iloc[0])


def write_episode_report(
    episodes: Episodes,
    run: Run,
    path: Path | str,
    *,
    split=None,
    gallery_size: int = 6,
) -> Path:
    """Condition tables plus a best/worst gallery, as one standalone HTML file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    features = episodes.features
    conditions = episodes.screen()
    threshold = episodes.threshold()
    n_features = len(episodes.setup_columns)

    profiles = "".join(
        f"<h3>{feature}</h3>" + episodes.profile(feature).round(3).to_html(border=0)
        for feature in conditions["feature"].head(PROFILED_FEATURES)
    )

    path_html = path_chart(episodes).to_html(full_html=False, include_plotlyjs=True)
    anatomy = excursion_summary(episodes).round(2).to_html(index=False, border=0)

    best = episodes.best(gallery_size).index.tolist()
    worst = episodes.worst(gallery_size).index.tolist()
    best_html = episode_gallery(
        episodes, best, title="Best episodes (highest gross return)"
    ).to_html(full_html=False, include_plotlyjs=False)
    worst_html = episode_gallery(
        episodes, worst, title="Worst episodes (lowest gross return)"
    ).to_html(full_html=False, include_plotlyjs=False)

    split_note = ""
    if split is not None:
        split_note = f"<p class=\"note\"><b>{split.name}</b>: {split.purpose}</p>"

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{run.config.run_id} — episode analysis</title>
<style>{REPORT_CSS}</style></head>
<body>
<h1>{run.config.run_id} — episode analysis</h1>
<p class="subtitle">{len(episodes)} round trips · gross win rate
{features['gross_win'].mean():.1%} · mean gross
{features['gross_return_bps'].mean():+.2f} bps · mean cost
{features['cost_bps'].mean():.2f} bps</p>
{split_note}

<h2>What the average episode does</h2>
{path_html}

<h2>How wins and losses got there</h2>
{anatomy}
<p class="note">Excursions are measured between entry and exit. A loser whose
mean MFE is well above zero was in profit at some point: that is an exit
problem. A loser that was never favourable is a signal problem.</p>

<h2>Conditions vs gross return</h2>
{conditions.round(3).to_html(index=False, border=0)}
<p class="note">{n_features} features were screened against one
outcome, so |t| must exceed <b>{threshold:.2f}</b> before a row means anything —
and overlapping trades in the same minute are not independent, which makes the
effective sample smaller than <i>n</i>. Anything surviving both is a candidate
to confirm on another window, not a finding.</p>

<h2>Quantile profiles of the strongest candidates</h2>
{profiles}

<h2>Winners vs losers</h2>
{episodes.contrast().round(3).to_html(index=False, border=0)}

<h2>Episode gallery</h2>
<p class="note">Prices are basis points from the entry, time is bars from the
entry, and short episodes are flipped so up is profit in every panel. Solid line
= entry, dotted = exit.</p>
{best_html}
{worst_html}
</body></html>
"""
    path.write_text(html, encoding="utf-8")
    return path
