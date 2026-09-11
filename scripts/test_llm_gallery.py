#!/usr/bin/env python3
"""Ask the LLM to judge exactly the best/worst trades in a setup gallery.

    python scripts/test_llm_gallery.py \
      --config config/backtest/sr_momentum_5min.yaml --split m5_mine \
      --model Qwen3.6:27b-mlx --n 20

Selection is deterministic and identical to ``plot_setups.py``: the ``n``
largest and ``n`` smallest completed trades by gross return. Each is mapped
back to the strategy's original position-run candidate, so the model sees only
information available at that candidate's decision bar, never the outcome.
"""

from __future__ import annotations

import argparse
import html
import json
import time
from dataclasses import replace

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401 (also sets sys.path)

from qtrader.analysis.episodes import extract_episodes
from qtrader.config import RunConfig
from qtrader.decision.candidates import Candidate, find_candidates
from qtrader.decision.client import DEFAULT_MODEL, OllamaClient
from qtrader.decision.journal import Journal
from qtrader.decision.validator import ValidationConfig, validate
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import execute
from qtrader.viz.report import REPORT_CSS
from qtrader.viz.setups import setup_gallery


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>{css}
.lede {{ max-width:64rem; margin:1.2rem auto; line-height:1.55; }}
.metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:.8rem; margin:1rem 0; }}
.metric {{ background:#f5f7f8; border-radius:8px; padding:.8rem; }}
.metric b {{ display:block; font-size:1.35rem; }}
table {{ font-size:.82rem; }} td.why {{ text-align:left; max-width:30rem; }}
.keep {{ color:#00897b; font-weight:600; }} .veto {{ color:#e53935; font-weight:600; }}
.abstain,.expired {{ color:#f57c00; font-weight:600; }}
</style></head><body><h1>{title}</h1>
<div class="lede">{lede}<div class="metrics">{metrics}</div>{table}</div>
{winners}{losers}</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--min-confidence", type=float, default=0.6)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--coarse-factor", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--out", default="setups_atr_acceptance_llm.html")
    parser.add_argument("--summary", default="llm_gallery_summary.json")
    parser.add_argument("--journal", default="llm_gallery_decisions.jsonl")
    parser.add_argument("--image-dir", default="llm_inputs",
                        help="keep the chart each candidate was judged on, under the run dir")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))
    run = execute(config)
    episodes = extract_episodes(run, context_bars=40)
    winners = list(episodes.best(args.n).index)
    losers = list(episodes.worst(args.n).index)

    mapping = _map_episodes(run, episodes, winners + losers)
    unique = {(c.symbol, c.timestamp, c.direction): c for c in mapping.values()}
    limited = _only_candidates(run.result.signals, list(unique.values()))

    journal = Journal(config.output_dir() / args.journal)
    client = OllamaClient(
        args.model, timeout=args.timeout, num_predict=args.num_predict
    )
    validation = ValidationConfig(
        min_confidence=args.min_confidence,
        on_abstain="keep",
        window=args.window,
        coarse_factor=args.coarse_factor,
        use_image=not args.no_image,
        image_dir=str(config.output_dir() / args.image_dir) if args.image_dir else None,
    )
    if hasattr(client, "preflight"):
        client.preflight()

    started = time.time()

    def progress(done, total, calls):
        elapsed = max(time.time() - started, 1e-9)
        rate = calls / elapsed
        eta = (total - done) / rate if rate else 0.0
        print(
            f"  {done:>2}/{total} · {calls} model calls · "
            f"{elapsed:.0f}s elapsed · {eta:.0f}s eta",
            flush=True,
        )

    print(
        f"{config.run_id}: judging {len(winners)} best + {len(losers)} worst "
        f"episodes with {args.model} (confidence {args.min_confidence:g})"
    )
    report = validate(
        limited, run.context, client, config=validation,
        journal=journal, progress=progress,
    )

    decisions = {
        (row["symbol"], pd.Timestamp(row["timestamp"]), int(row["direction"])): row
        for row in report.decisions
    }
    by_episode = {
        episode_id: decisions[(candidate.symbol, candidate.timestamp, candidate.direction)]
        for episode_id, candidate in mapping.items()
    }
    summary = _summarise(episodes, winners, losers, by_episode, args)
    summary_path = config.output_dir() / args.summary
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    _write_page(config, episodes, winners, losers, by_episode, summary, args)
    _print_summary(summary, summary_path, config.output_dir() / args.out)


def _map_episodes(run, episodes, episode_ids: list[str]) -> dict[str, Candidate]:
    """Map a filled round trip back to the position run that proposed it."""
    candidates = find_candidates(run.result.signals.target_weights)
    by_symbol: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_symbol.setdefault(candidate.symbol, []).append(candidate)

    positions = {stamp: i for i, stamp in enumerate(run.context.index)}
    lag = run.config.execution.execution_lag_bars
    mapped = {}
    for episode_id in episode_ids:
        row = episodes.features.loc[episode_id]
        entry_position = positions[pd.Timestamp(row["entry_time"])]
        active_at = max(entry_position - lag, 0)
        direction = 1 if row["direction"] == "LONG" else -1
        matched = [
            candidate for candidate in by_symbol.get(row["symbol"], [])
            if candidate.direction == direction
            and candidate.position <= active_at <= candidate.end_position
        ]
        if len(matched) != 1:
            raise RuntimeError(
                f"{episode_id}: expected one candidate at position {active_at}, "
                f"found {len(matched)}"
            )
        mapped[episode_id] = matched[0]
    return mapped


def _only_candidates(signals, selected: list[Candidate]):
    """A signal frame containing only the selected complete position runs."""
    source = signals.target_weights
    values = np.zeros(source.shape, dtype=float)
    columns = {name: i for i, name in enumerate(source.columns)}
    for candidate in selected:
        column = columns[candidate.symbol]
        values[candidate.position : candidate.end_position + 1, column] = source.iloc[
            candidate.position : candidate.end_position + 1, column
        ].to_numpy(dtype=float)
    limited = pd.DataFrame(values, index=source.index, columns=source.columns)
    return replace(signals, target_weights=limited)


def _summarise(episodes, winners, losers, decisions, args) -> dict:
    groups = {"winner": winners, "loser": losers}
    counts = {}
    items = []
    for group, episode_ids in groups.items():
        tally = {name: 0 for name in ("keep", "veto", "abstain", "expired")}
        for episode_id in episode_ids:
            decision = decisions[episode_id]
            tally[decision["action"]] += 1
            meta = episodes.features.loc[episode_id]
            items.append({
                "episode_id": episode_id,
                "group": group,
                "symbol": meta["symbol"],
                "direction": meta["direction"],
                "entry_time": meta["entry_time"],
                "gross_return_bps": float(meta["gross_return_bps"]),
                "action": decision["action"],
                "verdict": decision.get("verdict"),
                "error": decision.get("error", ""),
                "latency_s": decision.get("latency_s", 0.0),
            })
        counts[group] = {"total": len(episode_ids), **tally}

    loser_rate = counts["loser"]["veto"] / max(counts["loser"]["total"], 1)
    winner_rate = counts["winner"]["veto"] / max(counts["winner"]["total"], 1)
    vetoes = counts["loser"]["veto"] + counts["winner"]["veto"]
    return {
        "model": args.model,
        "min_confidence": args.min_confidence,
        "selection": f"{args.n} best and {args.n} worst by gross_return_bps",
        "counts": counts,
        "loser_veto_rate": loser_rate,
        "winner_false_veto_rate": winner_rate,
        "separation_pp": (loser_rate - winner_rate) * 100.0,
        "veto_precision": counts["loser"]["veto"] / vetoes if vetoes else None,
        "balanced_accuracy": 0.5 * (loser_rate + 1.0 - winner_rate),
        "items": items,
    }


def _write_page(config, episodes, winners, losers, decisions, summary, args) -> None:
    figures = {
        "winners": setup_gallery(
            episodes, winners, title=f"{args.n} best trades — LLM review",
            llm_actions=decisions, columns=4,
        ),
        "losers": setup_gallery(
            episodes, losers, title=f"{args.n} worst trades — LLM review",
            llm_actions=decisions, columns=4,
        ),
    }
    counts = summary["counts"]
    metrics = "".join([
        _metric("losers vetoed", f"{counts['loser']['veto']}/{counts['loser']['total']}"),
        _metric("winners falsely vetoed", f"{counts['winner']['veto']}/{counts['winner']['total']}"),
        _metric("veto-rate separation", f"{summary['separation_pp']:+.1f} pp"),
        _metric("balanced accuracy", f"{summary['balanced_accuracy']:.1%}"),
    ])
    lede = (
        f"<p>Qwen3.6 reviewed the exact {args.n} best and {args.n} worst episodes "
        f"using only information ending at each decision bar. Confidence threshold "
        f"{args.min_confidence:g}. A useful veto should reject losers while preserving "
        "winners; the difference between those rates is the key quantity.</p>"
        "<p>This is an outcome-selected visual audit of extreme trades, not an "
        "out-of-sample estimate over ordinary candidates.</p>"
    )
    path = config.output_dir() / args.out
    path.write_text(PAGE.format(
        title="sr_momentum — LLM review of gallery extremes",
        css=REPORT_CSS,
        lede=lede,
        metrics=metrics,
        table=_decision_table(summary["items"]),
        winners=figures["winners"].to_html(full_html=False, include_plotlyjs="cdn"),
        losers=figures["losers"].to_html(full_html=False, include_plotlyjs=False),
    ))


def _metric(label: str, value: str) -> str:
    return f'<div class="metric">{html.escape(label)}<b>{html.escape(value)}</b></div>'


def _decision_table(items: list[dict]) -> str:
    rows = []
    for item in items:
        verdict = item.get("verdict") or {}
        why = "; ".join(verdict.get("contradictions") or []) or verdict.get("rationale", "")
        confidence = verdict.get(
            "long_confidence" if item["direction"] == "LONG" else "short_confidence"
        )
        rows.append(
            f"<tr><td>{html.escape(item['group'])}</td>"
            f"<td>{html.escape(item['symbol'])}</td><td>{html.escape(item['direction'])}</td>"
            f"<td>{item['gross_return_bps']:+.0f}</td>"
            f"<td class='{item['action']}'>{html.escape(item['action'].upper())}</td>"
            f"<td>{'—' if confidence is None else f'{float(confidence):.2f}'}</td>"
            f"<td>{html.escape(verdict.get('regime', '—'))}</td>"
            f"<td class='why'>{html.escape(why[:180])}</td></tr>"
        )
    return (
        "<table><thead><tr><th>group</th><th>symbol</th><th>side</th>"
        "<th>gross bps</th><th>LLM</th><th>side confidence</th><th>regime</th>"
        f"<th>reason</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _print_summary(summary, summary_path, html_path) -> None:
    loser, winner = summary["counts"]["loser"], summary["counts"]["winner"]
    answered = sum(side[name] for side in (loser, winner) for name in ("keep", "veto"))
    total = loser["total"] + winner["total"]

    print("\n" + "=" * 72)
    if answered == 0:
        # Every call failed. Reporting 0 vetoes and 50% balanced accuracy here
        # would read exactly like a model that judged everything and objected to
        # nothing, which is the most misleading output this script could give.
        print(f"NO VERDICTS: all {total} calls failed — the numbers below would be")
        print("meaningless, so they are not shown. Check the model name and that")
        print("ollama is serving it; the journal's `error` field has the reason.")
        print("=" * 72)
        print(f"journal: {summary_path}")
        return
    if answered < total:
        print(f"WARNING: only {answered}/{total} candidates produced a verdict; "
              "the rest failed and are excluded from the rates below.")


    print(f"losers vetoed:          {loser['veto']}/{loser['total']} "
          f"({summary['loser_veto_rate']:.1%})")
    print(f"winners falsely vetoed: {winner['veto']}/{winner['total']} "
          f"({summary['winner_false_veto_rate']:.1%})")
    print(f"veto-rate separation:   {summary['separation_pp']:+.1f} percentage points")
    print(f"veto precision:          {summary['veto_precision']:.1%}"
          if summary["veto_precision"] is not None else "veto precision: no vetoes")
    print(f"balanced accuracy:       {summary['balanced_accuracy']:.1%}")
    print("=" * 72)
    print(f"summary: {summary_path}")
    print(f"gallery: {html_path}")


if __name__ == "__main__":
    main()
