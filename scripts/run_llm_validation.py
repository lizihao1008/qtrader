#!/usr/bin/env python3
"""Run a strategy in Baseline Mode and in LLM Enhanced Mode, and compare them.

    python scripts/run_llm_validation.py --config config/backtest/sr_momentum_5min.yaml \
        --split m5_mine --min-confidence 0.6

Both arms use the same data, the same strategy and the same candidate set. The
only difference is that the enhanced arm may *remove* entries — it can never add
one — so a difference in the result is attributable to the filter and not to a
different opportunity set.

Every decision is journalled to results/<run_id>/llm_decisions.jsonl and reused
on the next run, so a second invocation is free and needs no model. Interrupt it
and re-run: completed decisions are kept.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis import performance_summary
from qtrader.analysis.episodes import extract_episodes
from qtrader.config import RunConfig
from qtrader.decision.client import DEFAULT_MODEL, OllamaClient, ScriptedClient
from qtrader.decision.journal import Journal
from qtrader.decision.validator import ValidationConfig, validate
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.runner import build_context, execute


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--min-confidence", type=float, default=0.6)
    parser.add_argument("--on-abstain", default="keep", choices=["keep", "drop"])
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--coarse-factor", type=int, default=6)
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--num-predict", type=int, default=300)
    parser.add_argument("--latency-budget", type=float,
                        help="seconds from decision to fill; default = one bar")
    parser.add_argument("--max-calls", type=int, default=0, help="0 = no cap")
    parser.add_argument("--dry-run", action="store_true",
                        help="use a stub model that always agrees; no ollama needed")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    context = build_context(config)
    journal = Journal(config.output_dir() / "llm_decisions.jsonl")
    validation = ValidationConfig(
        min_confidence=args.min_confidence,
        on_abstain=args.on_abstain,
        window=args.window,
        coarse_factor=args.coarse_factor,
        use_image=not args.no_image,
        latency_budget_s=args.latency_budget,
        max_calls=args.max_calls,
    )
    client = (
        ScriptedClient([_AGREE], model="dry-run")
        if args.dry_run
        else OllamaClient(args.model, timeout=args.timeout, num_predict=args.num_predict)
    )
    if hasattr(client, "preflight"):
        client.preflight()

    print(f"{config.run_id} · {args.split} · model {client.model} · "
          f"min_confidence {validation.min_confidence} · on_abstain {validation.on_abstain}")

    baseline = execute(config, context=context)
    print(f"\nBaseline Mode: {len(baseline.result.trades):,} trades")

    started = time.time()
    report = {}

    def progress(done, total, calls):
        if calls and (done % 25 == 0 or done == total):
            rate = calls / max(time.time() - started, 1e-9)
            print(f"  {done:>6,}/{total:,}  {calls:,} calls  {rate*3600:5.0f}/h  "
                  f"eta {(total-done)/max(rate,1e-9)/3600:5.1f} h", flush=True)

    def validator(signals, ctx):
        result = validate(signals, ctx, client, config=validation,
                          journal=journal, progress=progress)
        report.update(result.counts)
        report["decisions"] = result.decisions
        return result

    enhanced = execute(config, context=context, validator=validator)
    counts = enhanced.result.metrics["validation"]
    print(f"\nLLM Enhanced Mode: {len(enhanced.result.trades):,} trades "
          f"({time.time()-started:.0f}s)")
    print(f"  candidates {counts['candidates']:,} · model calls {counts['model_calls']:,} · "
          f"median latency {counts['median_latency_s']}s")
    print(f"  keep {counts['keep']:,} · veto {counts['veto']:,} · "
          f"abstain {counts['abstain']:,} · expired {counts['expired']:,}")

    _compare(baseline, enhanced, report.get("decisions", []), config)


_AGREE = {"regime": "trend_up", "long_confidence": 0.9, "short_confidence": 0.9,
          "wait_confidence": 0.0, "supports_setup": True, "contradictions": []}


def _compare(baseline, enhanced, decisions, config) -> None:
    """The four arms the brief asks for, on identical data."""
    rows = []
    for name, run in (("Quant baseline", baseline), ("Quant + LLM", enhanced)):
        episodes = extract_episodes(run, context_bars=2)
        if not len(episodes):
            rows.append({"arm": name, "trades": 0})
            continue
        table = performance_summary(run.result, episodes)
        rows.append({
            "arm": name,
            "trades": int(table.loc["all", "trades"]),
            "hit_rate": table.loc["all", "hit_rate"],
            "gross_bps": table.loc["all", "gross_bps"],
            "net_bps": table.loc["all", "expectancy_bps"],
            "total_return": run.result.metrics.get("total_return"),
            "sharpe": run.result.metrics.get("sharpe"),
            "max_drawdown": run.result.metrics.get("max_drawdown"),
        })

    # The two counterfactual arms: what the LLM kept, and what it threw away,
    # both measured on the *baseline* fills so they are directly comparable.
    accepted = {(d["symbol"], pd.Timestamp(d["timestamp"])) for d in decisions
                if d["action"] == "keep"}
    rejected = {(d["symbol"], pd.Timestamp(d["timestamp"])) for d in decisions
                if d["action"] in ("veto", "expired")}
    base_episodes = extract_episodes(baseline, context_bars=2)
    if len(base_episodes):
        features = base_episodes.features
        lag = baseline.config.execution.execution_lag_bars
        index = baseline.context.panel.index
        position = {t: i for i, t in enumerate(index)}
        decided = [index[position[pd.Timestamp(t)] - lag] for t in features["entry_time"]]
        keys = list(zip(features["symbol"], decided))
        for name, chosen in (("  LLM accepted", accepted), ("  LLM rejected", rejected)):
            mask = np.array([k in chosen for k in keys])
            if mask.sum() < 5:
                continue
            side = features[mask]
            rows.append({
                "arm": name,
                "trades": int(mask.sum()),
                "hit_rate": float((side["gross_return_bps"] > 0).mean()),
                "gross_bps": float(side["gross_return_bps"].mean()),
                "net_bps": float(side["gross_return_bps"].mean() - 3.0),
            })

    table = pd.DataFrame(rows).set_index("arm")
    print("\n" + "=" * 78)
    print(table.round(4).to_string())
    print("=" * 78)

    path = config.output_dir() / "llm_comparison.json"
    path.write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nwrote {path}")
    print("     " + str(config.output_dir() / "llm_decisions.jsonl"))


if __name__ == "__main__":
    main()
