"""Self-contained HTML backtest report.

One file per run: headline metrics, signal-quality diagnostics, K-line charts
with buy/sell markers for the selected symbols, the cumulative-return curve, the
exposure profile, per-symbol attribution and the trade log. Plotly.js is
embedded so the report opens offline and can be archived with the run.

Only a few symbols get a K-line chart. A 22-symbol universe at 1-minute
resolution would produce a report too large to open, and the charts exist to
inspect *behaviour*, which a couple of representative symbols already show.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestResult
from .charts import equity_chart, exposure_chart, price_chart

#: Most recent trades listed in full; the rest are summarised per symbol.
MAX_TRADE_ROWS = 250

#: metric key -> (label, format). Anything not listed is omitted.
_METRIC_FORMATS = {
    "total_return": ("Total return", "pct"),
    "benchmark_return": ("Benchmark return", "pct"),
    "annualized_return": ("Annualized return", "pct"),
    "annualized_volatility": ("Annualized volatility", "pct"),
    "sharpe": ("Sharpe", "num"),
    "sortino": ("Sortino", "num"),
    "max_drawdown": ("Max drawdown", "pct"),
    "calmar": ("Calmar", "num"),
    "n_trades": ("Trades", "int"),
    "hit_rate": ("Hit rate", "pct"),
    "profit_factor": ("Profit factor", "num"),
    "avg_trade_return": ("Avg trade return", "pct"),
    "daily_turnover": ("Daily turnover", "num"),
    "total_costs": ("Total costs ($)", "money"),
    "gross_pnl": ("Gross PnL ($)", "money"),
    "net_pnl": ("Net PnL ($)", "money"),
    "avg_gross_exposure": ("Avg gross exposure", "pct"),
    "avg_net_exposure": ("Avg net exposure", "pct"),
    "avg_positions": ("Avg positions", "num"),
    "time_in_market": ("Time in market", "pct"),
    "n_bars": ("Bars", "int"),
    "n_sessions": ("Sessions", "int"),
}

_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, sans-serif;
       margin: 0 auto; max-width: 1280px; padding: 24px;
       color: #263238; background: #ffffff; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 17px; margin-top: 32px; border-bottom: 1px solid #eceff1; padding-bottom: 6px; }
.subtitle { color: #607d8b; font-size: 13px; margin-top: 0; }
.note { color: #607d8b; font-size: 12px; margin: 6px 0 0; }
.metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
.metric { background: #f5f7f8; border-radius: 8px; padding: 10px 12px; }
.metric .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #607d8b; }
.metric .value { font-size: 18px; font-weight: 600; margin-top: 2px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { padding: 6px 8px; border-bottom: 1px solid #eceff1; text-align: right; }
th:first-child, td:first-child { text-align: left; }
thead th { background: #f5f7f8; position: sticky; top: 0; }
.scroll { max-height: 420px; overflow: auto; border: 1px solid #eceff1; border-radius: 8px; }
"""


def _format_metric(value, kind: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if kind == "pct":
        return f"{value * 100:,.2f}%"
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "int":
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _metrics_html(metrics: dict) -> str:
    cards = [
        f'<div class="metric"><div class="label">{label}</div>'
        f'<div class="value">{_format_metric(metrics[key], kind)}</div></div>'
        for key, (label, kind) in _METRIC_FORMATS.items()
        if key in metrics
    ]
    return f'<div class="metrics">{"".join(cards)}</div>'


def _signal_html(metrics: dict) -> str:
    """Rank IC table — did the score order the cross-section correctly?"""
    summary = metrics.get("rank_ic")
    if not summary:
        return ""
    rows = []
    for horizon, stats in summary.items():
        if not stats.get("n_obs"):
            continue
        rows.append(
            "<tr>"
            f"<td>{horizon}</td>"
            f"<td>{stats['mean_ic']:.5f}</td>"
            f"<td>{stats['ic_ir']:.3f}</td>"
            f"<td>{stats['t_stat']:.1f}</td>"
            f"<td>{stats['share_positive'] * 100:.1f}%</td>"
            f"<td>{stats['n_obs']:,}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return (
        "<h2>Signal quality (rank IC vs demeaned forward return)</h2>"
        "<table><thead><tr><th>Horizon</th><th>Mean IC</th><th>IC IR</th>"
        "<th>t-stat</th><th>% positive</th><th>Observations</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">Overlapping windows make consecutive observations '
        "correlated, so the t-statistic overstates significance. Use it to "
        "screen, not to conclude.</p>"
    )


def _attribution_html(trades: pd.DataFrame) -> str:
    """Where the PnL came from, by symbol."""
    if trades.empty:
        return ""
    grouped = (
        trades.groupby("symbol")
        .agg(
            trades=("net_pnl", "size"),
            net_pnl=("net_pnl", "sum"),
            gross_pnl=("gross_pnl", "sum"),
            costs=("costs", "sum"),
            hit_rate=("net_pnl", lambda s: (s > 0).mean()),
        )
        .sort_values("net_pnl", ascending=False)
    )
    grouped["hit_rate"] = (grouped["hit_rate"] * 100).round(1)
    numeric = ["net_pnl", "gross_pnl", "costs"]
    grouped[numeric] = grouped[numeric].round(2)
    return (
        "<h2>Attribution by symbol</h2>"
        f'<div class="scroll">{grouped.to_html(border=0)}</div>'
    )


def _trades_html(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "<p>No round-trip trades were completed.</p>"

    table = trades.sort_values("exit_time").tail(MAX_TRADE_ROWS).copy()
    for column in ("entry_time", "exit_time"):
        table[column] = pd.DatetimeIndex(table[column]).tz_convert(
            "America/New_York"
        ).strftime("%Y-%m-%d %H:%M")
    table["return_pct"] = (table["return_pct"] * 100).round(3)
    numeric = table.select_dtypes("number").columns
    table[numeric] = table[numeric].round(4)

    note = ""
    if len(trades) > MAX_TRADE_ROWS:
        note = (
            f'<p class="note">Showing the most recent {MAX_TRADE_ROWS} of '
            f"{len(trades):,} trades; the full log is in trades.csv.</p>"
        )
    return f'{note}<div class="scroll">{table.to_html(index=False, border=0)}</div>'


def choose_report_symbols(result: BacktestResult, requested: tuple[str, ...]) -> list[str]:
    """Which symbols get a K-line chart: the configured ones, or the most traded."""
    if requested:
        return [s for s in requested if s in result.panel.symbols]
    return result.traded_symbols[:1]


def write_report(
    result: BacktestResult,
    path: Path | str,
    *,
    subtitle: str = "",
    symbols: tuple[str, ...] = (),
) -> Path:
    """Render the run to a standalone HTML file and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Plotly.js is embedded exactly once, by the first figure in document order.
    equity_html = equity_chart(result).to_html(full_html=False, include_plotlyjs=True)
    exposure_html = exposure_chart(result).to_html(full_html=False, include_plotlyjs=False)

    sections = [
        f"<h2>{symbol} — price and executed trades</h2>"
        + price_chart(result, symbol).to_html(full_html=False, include_plotlyjs=False)
        for symbol in choose_report_symbols(result, symbols)
    ]

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{result.name} backtest report</title>
<style>{_CSS}</style></head>
<body>
<h1>{result.name} — backtest report</h1>
<p class="subtitle">{subtitle}</p>

<h2>Metrics (net of costs)</h2>
{_metrics_html(result.metrics)}

{_signal_html(result.metrics)}

<h2>Cumulative return over time</h2>
{equity_html}

<h2>Exposure</h2>
{exposure_html}

{"".join(sections)}

{_attribution_html(result.trades)}

<h2>Trade log</h2>
{_trades_html(result.trades)}
</body></html>
"""
    path.write_text(html, encoding="utf-8")
    return path
