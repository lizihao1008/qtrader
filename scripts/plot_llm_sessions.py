#!/usr/bin/env python3
"""Run one test per session and chart each, with the LLM's verdicts marked.

    python scripts/plot_llm_sessions.py --config config/backtest/sr_momentum_iren_5min.yaml \
        --symbol IREN --sessions 5

Each session is its own backtest: history before it is loaded for warm-up, but
only that day's trades are reported and charted. The strategy is flat by 15:50,
so nothing carries overnight and the sessions really are independent tests.

Reuses `charts.price_chart` for the panel — candles, volume, MACD, the
strategy's own view and every executed fill — and adds one layer on top: what
the LLM decided at each candidate bar.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.config import RunConfig
from qtrader.data.sessions import session_date
from qtrader.decision.client import DEFAULT_MODEL, OllamaClient, ScriptedClient
from qtrader.decision.journal import Journal
from qtrader.decision.validator import ValidationConfig, ValidationReport, validate
from qtrader.runner import build_context, execute
from qtrader.viz.charts import price_chart
from qtrader.viz.llm import annotate_decisions, decision_table
from qtrader.viz.report import REPORT_CSS

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>{css}
.session {{ margin: 2.5rem auto; max-width: 78rem; }}
.summary {{ display:flex; gap:2rem; flex-wrap:wrap; margin:.5rem 0 1rem; }}
.summary div {{ font-size:.92rem; }}
.summary b {{ display:block; font-size:1.25rem; }}
table.verdicts {{ border-collapse:collapse; font-size:.85rem; margin:.5rem 0 1rem; }}
table.verdicts th, table.verdicts td {{ border-bottom:1px solid #eceff1; padding:.32rem .7rem;
  text-align:left; }}
table.verdicts th {{ color:#607d8b; font-weight:600; }}
td.why {{ color:#546e7a; max-width:34rem; }}
.muted {{ color:#78909c; }}
.lede {{ max-width:60rem; margin:1.2rem auto; line-height:1.55; }}
</style></head><body>
<h1>{title}</h1>
<div class="lede"><p>{lede}</p>
<p>★ the LLM agreed and the trade stood · ✕ the LLM vetoed it · ○ abstained ·
each marker sits on the bar the decision was taken, one bar before the fill.
Hover any marker for the model's long/short/wait confidences and its reasoning.</p></div>
{sessions}
</body></html>"""

SESSION = """<div class="session"><h2>{date} — {weekday}</h2>
<div class="summary">
  <div>candidates<b>{candidates}</b></div>
  <div>LLM kept<b>{kept}</b></div>
  <div>LLM vetoed<b>{vetoed}</b></div>
  <div>baseline trades<b>{base_trades}</b></div>
  <div>baseline P&amp;L<b>{base_pnl}</b></div>
  <div>with LLM<b>{llm_trades}</b></div>
  <div>LLM P&amp;L<b>{llm_pnl}</b></div>
</div>
{table}
{chart}
</div>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sessions", type=int, default=5, help="most recent N sessions")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--min-confidence", type=float, default=0.6)
    parser.add_argument("--on-abstain", default="keep", choices=["keep", "drop"])
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="llm_sessions.html")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config).with_overrides(parse_assignments(args.set))
    context = build_context(config)
    sessions = sorted(set(session_date(context.index)))[-args.sessions:]
    print(f"{config.run_id}: testing {len(sessions)} sessions — "
          f"{sessions[0]} to {sessions[-1]}")

    journal = Journal(config.output_dir() / "llm_decisions.jsonl")
    validation = ValidationConfig(min_confidence=args.min_confidence,
                                  on_abstain=args.on_abstain,
                                  use_image=not args.no_image)
    client = (ScriptedClient([_AGREE_LONG], model="dry-run") if args.dry_run
              else OllamaClient(args.model))

    blocks, totals = [], {"base": 0.0, "llm": 0.0, "kept": 0, "vetoed": 0, "candidates": 0}
    _FIRST.append(sessions[0])
    for day in sessions:
        block, tally = _session(config, context, day, client, validation, journal, args)
        blocks.append(block)
        for key in totals:
            totals[key] += tally[key]
        print(f"  {day}: {tally['candidates']} candidates, {tally['kept']} kept, "
              f"{tally['vetoed']} vetoed | baseline {tally['base']:+.0f} "
              f"vs LLM {tally['llm']:+.0f}")

    lede = (
        f"{args.symbol}, {len(sessions)} sessions, one independent test each. "
        f"{totals['candidates']} entry candidates: the LLM kept {totals['kept']} and "
        f"vetoed {totals['vetoed']} at a confidence threshold of "
        f"{args.min_confidence:g}. Baseline P&amp;L {totals['base']:+,.0f} against "
        f"{totals['llm']:+,.0f} with the LLM filter."
    )
    path = config.output_dir() / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE.format(
        title=f"{args.symbol} — quant entries and LLM verdicts",
        css=REPORT_CSS, lede=lede, sessions="\n".join(blocks),
    ))
    print(f"\nwrote {path}")


_AGREE_LONG = {"regime": "trend_up", "long_confidence": 0.85, "short_confidence": 0.1,
               "wait_confidence": 0.05, "supports_setup": True, "contradictions": []}


def _session(config, context, day, client, validation, journal, args):
    """One day: baseline run, LLM run, and the chart of that session."""
    index = context.index
    day_mask = session_date(index) == day
    # Warm-up history is loaded, but this session is the test: entries are only
    # judged and reported here.
    window = index[day_mask]

    decisions: list[dict] = []

    def only_this_session(signals):
        """Both arms trade this session and nothing else.

        Without this the baseline trades the whole loaded window and reaches the
        test day on a different equity, so position sizes differ and the two
        arms are no longer the same experiment — they disagreed by a few dollars
        even when the LLM kept every candidate, which is what exposed it.
        """
        limited = signals.target_weights.copy()
        limited.loc[~day_mask] = 0.0
        return replace(signals, target_weights=limited)

    def session_only(signals, ctx):
        return ValidationReport(signals=only_this_session(signals))

    def validator(signals, ctx):
        report = validate(only_this_session(signals), ctx, client,
                          config=validation, journal=journal)
        decisions.extend(report.decisions)
        return report

    baseline = execute(config, context=context, validator=session_only)
    enhanced = execute(config, context=context, validator=validator)

    base_trades = _day_trades(baseline, day, args.symbol)
    llm_trades = _day_trades(enhanced, day, args.symbol)
    kept = sum(d["action"] == "keep" for d in decisions)
    vetoed = sum(d["action"] in ("veto", "expired") for d in decisions)

    figure = price_chart(enhanced.result, args.symbol, height=760,
                         window=(window[0], window[-1]))
    figure.update_layout(title=f"{args.symbol} — {day}")
    annotate_decisions(figure, decisions, context.panel.close[args.symbol],
                       symbol=args.symbol, session=str(day))

    block = SESSION.format(
        date=day, weekday=pd.Timestamp(day).day_name(),
        candidates=len(decisions), kept=kept, vetoed=vetoed,
        base_trades=len(base_trades), llm_trades=len(llm_trades),
        base_pnl=f"{base_trades['net_pnl'].sum():+,.0f}" if len(base_trades) else "—",
        llm_pnl=f"{llm_trades['net_pnl'].sum():+,.0f}" if len(llm_trades) else "—",
        table=decision_table(decisions, symbol=args.symbol, session=str(day)),
        chart=figure.to_html(full_html=False,
                             include_plotlyjs="cdn" if day == _FIRST[0] else False),
    )
    return block, {
        "candidates": len(decisions), "kept": kept, "vetoed": vetoed,
        "base": float(base_trades["net_pnl"].sum()) if len(base_trades) else 0.0,
        "llm": float(llm_trades["net_pnl"].sum()) if len(llm_trades) else 0.0,
    }


_FIRST: list = []


def _day_trades(run, day, symbol) -> pd.DataFrame:
    """That session's round trips in one symbol.

    `session_date` returns a Series keyed by the timestamps it was given, so its
    comparison has to be taken to a plain array before it can mask a trades
    frame indexed 0..n — otherwise pandas aligns on the index and the mask is
    silently empty.
    """
    trades = run.result.trades
    if trades.empty:
        return trades
    same_day = session_date(pd.DatetimeIndex(trades["entry_time"])).to_numpy() == day
    return trades.loc[same_day & (trades["symbol"] == symbol).to_numpy()]


def _naive(stamp):
    return pd.Timestamp(stamp).tz_convert("America/New_York").tz_localize(None)


if __name__ == "__main__":
    main()
