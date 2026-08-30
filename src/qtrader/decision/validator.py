"""Running the layer: candidates in, a narrowed weight frame out.

    strategy.generate()  ->  signals
                             validate(signals, ...)   <- here
                             engine.run()

The contract is deliberately small. `validate` may zero position runs and may do
nothing else: it cannot open a position, change a direction, or alter a weight's
magnitude. Everything downstream — the liquidity mask, the engine's printed and
eligible guards, whole-share rounding — is untouched and still has the last word.

Latency is treated as real. A verdict that would not have returned before the
execution bar's open is `EXPIRED` and the candidate is dropped, because in live
trading the order would simply not have been sent in time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import pandas as pd

from ..strategies.base import StrategySignals
from . import chart as chart_module
from . import prompt as prompt_module
from .candidates import Candidate, apply_vetoes, find_candidates
from .journal import Journal, decision_key
from .schema import Action, decide
from .snapshot import DEFAULT_COARSE_FACTOR, DEFAULT_WINDOW, build_snapshot


@dataclass(frozen=True)
class ValidationConfig:
    """How the layer behaves. Every safety choice is one field here."""

    #: Directional conviction the model must have in the strategy's own side.
    min_confidence: float = 0.6

    #: What an unusable verdict does. `keep` leaves the baseline trade in place,
    #: so an unavailable model cannot silently mutate the strategy; `drop` is the
    #: fail-closed posture for live trading.
    on_abstain: str = "keep"

    #: Bars of execution-timeframe context shown to the model.
    window: int = DEFAULT_WINDOW

    #: Execution bars per higher-timeframe bar.
    coarse_factor: int = DEFAULT_COARSE_FACTOR

    #: Attach the K-line image.
    use_image: bool = True

    #: Seconds available between the decision bar's close and the fill. `None`
    #: derives it from the bar interval, which is the honest live constraint.
    latency_budget_s: float | None = None

    #: Stop after this many *model calls* (cached rows are free). 0 means no cap.
    max_calls: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must lie in [0, 1]")
        if self.on_abstain not in ("keep", "drop"):
            raise ValueError("on_abstain must be 'keep' or 'drop'")


@dataclass
class ValidationReport:
    """What the layer did, for the comparison harness and the run manifest."""

    signals: StrategySignals
    decisions: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def kept(self) -> list[dict]:
        return [d for d in self.decisions if d["action"] == Action.KEEP.value]

    @property
    def vetoed(self) -> list[dict]:
        return [d for d in self.decisions if d["action"] == Action.VETO.value]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.decisions)


def validate(
    signals: StrategySignals,
    context,
    client,
    *,
    config: ValidationConfig | None = None,
    journal: Journal | None = None,
    progress=None,
) -> ValidationReport:
    """Judge every candidate entry and return signals with the rejects removed."""
    config = config or ValidationConfig()
    candidates = find_candidates(signals.target_weights)
    budget = _latency_budget(context.index, config)
    cached = journal.load() if journal else {}
    model = getattr(client, "model", "unknown")

    decisions: list[dict] = []
    vetoed: list[Candidate] = []
    calls = 0

    for number, candidate in enumerate(candidates, start=1):
        key = decision_key(candidate, prompt_version=prompt_module.PROMPT_VERSION,
                           model=model)
        if key in cached:
            row = cached[key]
        else:
            if config.max_calls and calls >= config.max_calls:
                break
            row = _judge(candidate, signals, context, client, config, budget, key, model)
            calls += 1
            if journal:
                journal.append(row)

        decisions.append(row)
        if row["action"] in (Action.VETO.value, Action.EXPIRED.value):
            vetoed.append(candidate)
        elif row["action"] == Action.ABSTAIN.value and config.on_abstain == "drop":
            vetoed.append(candidate)

        if progress:
            progress(number, len(candidates), calls)

    narrowed = replace(signals, target_weights=apply_vetoes(signals.target_weights, vetoed))
    return ValidationReport(
        signals=narrowed,
        decisions=decisions,
        counts=_counts(decisions, len(candidates), calls, len(vetoed)),
    )


def _judge(candidate, signals, context, client, config, budget, key, model) -> dict:
    """One candidate: snapshot, prompt, call, decision — recorded whatever happens."""
    started = time.perf_counter()
    snapshot = build_snapshot(
        candidate, context.panel, signals.indicators,
        window=config.window, coarse_factor=config.coarse_factor,
    )
    row = {
        "key": key,
        "symbol": candidate.symbol,
        "timestamp": candidate.timestamp.isoformat(),
        "direction": candidate.direction,
        "weight": candidate.weight,
        "hold_bars": candidate.hold_bars,
        "prompt_version": prompt_module.PROMPT_VERSION,
        "model": model,
        "min_confidence": config.min_confidence,
        "snapshot": snapshot.as_record(),
    }

    if not snapshot.sufficient:
        # Not enough history to judge. An abstention, never a guess.
        return {**row, "action": _abstain(config), "verdict": None,
                "error": snapshot.reason, "latency_s": 0.0,
                "prompt_chars": 0, "image_bytes": 0}

    text = prompt_module.build(snapshot, coarse_factor=config.coarse_factor)
    image = chart_module.render(snapshot) if config.use_image else b""
    reply = client.ask(text, image)
    elapsed = time.perf_counter() - started

    row |= {
        "prompt_chars": len(text),
        "image_bytes": len(image),
        "latency_s": round(reply.latency_s, 3),
        "wall_s": round(elapsed, 3),
        "options": getattr(reply, "options", {}),
        "raw": reply.raw[:2000],
        "error": reply.error,
    }

    if budget is not None and reply.latency_s > budget:
        # Live, this order would have missed its bar. Dropping it in the
        # backtest is the only honest way to price that.
        return {**row, "action": Action.EXPIRED.value, "verdict": None,
                "error": row["error"] or f"latency {reply.latency_s:.1f}s > budget {budget:.1f}s"}
    if not reply.ok:
        return {**row, "action": _abstain(config), "verdict": None}

    action = decide(reply.verdict, candidate.direction,
                    min_confidence=config.min_confidence)
    return {**row, "action": action.value, "verdict": reply.verdict.as_record()}


def _abstain(config) -> str:
    return Action.ABSTAIN.value


def _latency_budget(index, config) -> float | None:
    """Seconds between the decision bar's close and the execution bar's open."""
    if config.latency_budget_s is not None:
        return config.latency_budget_s
    if len(index) < 2:
        return None
    return float((index[1] - index[0]).total_seconds())


def _counts(decisions, candidates: int, calls: int, vetoed: int) -> dict:
    tally = {action.value: 0 for action in Action}
    for row in decisions:
        tally[row["action"]] = tally.get(row["action"], 0) + 1
    latencies = [row["latency_s"] for row in decisions if row.get("latency_s")]
    return {
        "candidates": candidates,
        "judged": len(decisions),
        "model_calls": calls,
        "removed": vetoed,
        **tally,
        "median_latency_s": round(sorted(latencies)[len(latencies) // 2], 3)
        if latencies else 0.0,
    }
