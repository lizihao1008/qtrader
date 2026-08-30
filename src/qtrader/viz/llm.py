"""Drawing what the LLM said, on top of the chart the strategy produced.

`charts.price_chart` already draws candles, volume, MACD, the strategy's own
panel and every executed fill. The LLM layer adds exactly one thing to that
picture: at each bar the strategy proposed an entry, what the model decided and
how confident it was on each side.

Kept as an annotator rather than a new chart so the price panel stays the one
everything else in the project uses — a decision plotted against a different
chart is a decision you cannot compare to anything.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ..decision.schema import Action
from .charts import LONG_COLOR, SHORT_COLOR, _market_naive

#: One colour per action, so a glance at the panel says what happened.
ACTION_STYLE = {
    Action.KEEP.value: ("#26a69a", "star", "LLM kept"),
    Action.VETO.value: ("#ef5350", "x-thin-open", "LLM vetoed"),
    Action.ABSTAIN.value: ("#ffa726", "circle-open", "LLM abstained"),
    Action.EXPIRED.value: ("#8d6e63", "hourglass", "LLM too slow"),
}


def annotate_decisions(
    figure: go.Figure,
    decisions: list[dict],
    prices: pd.Series,
    *,
    symbol: str,
    row: int = 1,
    session: str | None = None,
) -> go.Figure:
    """Mark every judged candidate on an existing price panel.

    ``decisions`` are journal rows. ``prices`` is the close series the chart was
    drawn from, used only to place each marker at its own bar's price.
    """
    rows = [d for d in decisions if d["symbol"] == symbol]
    if session:
        rows = [d for d in rows if str(pd.Timestamp(d["timestamp"]).date()) == session]
    if not rows:
        return figure

    for action, (colour, marker, label) in ACTION_STYLE.items():
        subset = [d for d in rows if d["action"] == action]
        if not subset:
            continue

        stamps = [pd.Timestamp(d["timestamp"]) for d in subset]
        available = [t for t in stamps if t in prices.index]
        if not available:
            continue
        subset = [d for d, t in zip(subset, stamps) if t in prices.index]

        figure.add_trace(
            go.Scatter(
                x=_market_naive(pd.DatetimeIndex(available)),
                y=[prices.loc[t] for t in available],
                mode="markers",
                name=label,
                marker=dict(symbol=marker, size=15, color=colour,
                            line=dict(width=1.6, color=colour)),
                customdata=[_hover(d) for d in subset],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>%{customdata[1]}<br>"
                    "long %{customdata[2]}  short %{customdata[3]}  wait %{customdata[4]}"
                    "<br>regime %{customdata[5]}<br>%{customdata[6]}<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )
    return figure


def decision_table(decisions: list[dict], *, symbol: str, session: str | None = None) -> str:
    """A compact HTML table of the verdicts, to sit beside the chart."""
    rows = [d for d in decisions if d["symbol"] == symbol]
    if session:
        rows = [d for d in rows if str(pd.Timestamp(d["timestamp"]).date()) == session]
    if not rows:
        return "<p class='muted'>No entry candidates in this session.</p>"

    head = ("<tr><th>decision bar</th><th>quant</th><th>LLM</th><th>long</th>"
            "<th>short</th><th>wait</th><th>regime</th><th>why</th></tr>")
    body = []
    for d in sorted(rows, key=lambda r: r["timestamp"]):
        verdict = d.get("verdict") or {}
        colour, _, label = ACTION_STYLE.get(d["action"], ("#607d8b", "circle", d["action"]))
        local = pd.Timestamp(d["timestamp"]).tz_convert("America/New_York")
        side = "LONG" if d["direction"] > 0 else "SHORT"
        why = ("; ".join(verdict.get("contradictions") or [])
               or verdict.get("rationale", "")
               or d.get("error", ""))
        body.append(
            f"<tr><td>{local:%H:%M}</td><td>{side}</td>"
            f"<td style='color:{colour};font-weight:600'>{label.replace('LLM ','')}</td>"
            f"<td>{_num(verdict.get('long_confidence'))}</td>"
            f"<td>{_num(verdict.get('short_confidence'))}</td>"
            f"<td>{_num(verdict.get('wait_confidence'))}</td>"
            f"<td>{verdict.get('regime','—')}</td><td class='why'>{why[:160]}</td></tr>"
        )
    return f"<table class='verdicts'>{head}{''.join(body)}</table>"


def _hover(row: dict) -> list:
    verdict = row.get("verdict") or {}
    label = ACTION_STYLE.get(row["action"], ("", "", row["action"]))[2]
    why = ("; ".join(verdict.get("contradictions") or [])
           or verdict.get("rationale", "") or row.get("error", ""))
    return [
        label,
        f"quant proposed {'LONG' if row['direction'] > 0 else 'SHORT'}",
        _num(verdict.get("long_confidence")),
        _num(verdict.get("short_confidence")),
        _num(verdict.get("wait_confidence")),
        verdict.get("regime", "—"),
        why[:120],
    ]


def _num(value) -> str:
    return "—" if value is None else f"{float(value):.2f}"
