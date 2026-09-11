"""The layer end to end, with a scripted model in place of ollama.

Two properties carry the whole design and are tested first: the snapshot cannot
see past the decision bar, and every failure mode ends in an abstention rather
than a trade.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qtrader.decision.candidates import Candidate, find_candidates
from qtrader.decision.client import Reply, ScriptedClient
from qtrader.decision.journal import Journal, decision_key
from qtrader.decision.schema import Action
from qtrader.decision.snapshot import build_snapshot, coarsen
from qtrader.decision.validator import ValidationConfig, validate
from qtrader.strategies.base import StrategySignals
from tests.conftest import make_context

BARS = 90


def scene(weights=None):
    """A context plus signals with one long candidate at bar 60."""
    rng = np.random.default_rng(3)
    path = list(100.0 + np.cumsum(rng.normal(0.0, 0.05, BARS)))
    context = make_context({"AAA": path, "SPY": [400.0] * BARS})

    column = np.zeros(BARS)
    column[60:70] = 0.1
    target = pd.DataFrame({"AAA": weights if weights is not None else column},
                          index=context.index)
    indicators = {"AAA": pd.DataFrame(
        {"momentum_z": np.linspace(-1, 2, BARS), "relative_volume": 1.5},
        index=context.index)}
    return context, StrategySignals(target_weights=target, indicators=indicators)


def agreeing(long=0.8, short=0.05, supports=True) -> dict:
    return {"regime": "trend_up", "long_confidence": long, "short_confidence": short,
            "wait_confidence": 0.1, "supports_setup": supports, "contradictions": []}


# ------------------------------------------------------------------ causality
def test_the_snapshot_stops_at_the_decision_bar():
    """The single most important property in this module."""
    context, signals = scene()
    (candidate,) = find_candidates(signals.target_weights)
    snapshot = build_snapshot(candidate, context.panel, signals.indicators, window=20)

    closes = context.panel.close["AAA"]
    assert snapshot.bars.index[-1] == context.index[candidate.position]
    assert snapshot.bars["close"].iloc[-1] == pytest.approx(closes.iloc[candidate.position])
    future = set(np.round(closes.iloc[candidate.position + 1:].to_numpy(), 10))
    shown = set(np.round(snapshot.bars["close"].dropna().to_numpy(), 10))
    assert not (shown & future), "a future close reached the snapshot"


def test_the_indicator_values_are_read_at_the_decision_bar():
    context, signals = scene()
    (candidate,) = find_candidates(signals.target_weights)
    snapshot = build_snapshot(candidate, context.panel, signals.indicators, window=20)

    expected = signals.indicators["AAA"]["momentum_z"].iloc[candidate.position]
    assert snapshot.indicators["momentum_z"] == pytest.approx(expected)


def test_the_coarse_view_ends_on_a_completed_bar():
    """Grouping forward would leave a partial, still-forming candle at the end."""
    index = pd.date_range("2026-01-02 14:30", periods=10, freq="5min", tz="UTC")
    bars = pd.DataFrame({"open": np.arange(10.0), "high": np.arange(10.0) + 1,
                         "low": np.arange(10.0) - 1, "close": np.arange(10.0),
                         "volume": 100.0}, index=index)
    coarse = coarsen(bars, 3)

    assert coarse.index[-1] == index[-1], "the last coarse bar must end at the decision bar"
    assert coarse["close"].iloc[-1] == bars["close"].iloc[-1]
    assert coarse["high"].iloc[-1] == bars["high"].iloc[-3:].max()


# ------------------------------------------------------------------- the flow
def test_agreement_keeps_the_position_untouched():
    context, signals = scene()
    report = validate(signals, context, ScriptedClient([agreeing()]))

    pd.testing.assert_frame_equal(report.signals.target_weights, signals.target_weights)
    assert report.counts["keep"] == 1 and report.counts["removed"] == 0


def test_low_confidence_removes_the_whole_holding():
    context, signals = scene()
    report = validate(signals, context, ScriptedClient([agreeing(long=0.55)]))

    assert (report.signals.target_weights["AAA"] == 0).all()
    assert report.counts["veto"] == 1 and report.counts["removed"] == 1


def test_the_model_disagreeing_removes_the_position():
    context, signals = scene()
    report = validate(signals, context, ScriptedClient([agreeing(long=0.1, short=0.9)]))
    assert (report.signals.target_weights["AAA"] == 0).all()


def test_the_threshold_is_configurable():
    context, signals = scene()
    reply = [agreeing(long=0.65)]
    strict = validate(signals, context, ScriptedClient(reply),
                      config=ValidationConfig(min_confidence=0.7))
    loose = validate(signals, context, ScriptedClient(reply),
                     config=ValidationConfig(min_confidence=0.6))

    assert strict.counts["veto"] == 1
    assert loose.counts["keep"] == 1


# --------------------------------------------------------------- failing safe
@pytest.mark.parametrize("reply", [
    {"nonsense": True},                              # schema violation
    {"regime": "trend_up", "long_confidence": 5.0,   # out of contract
     "short_confidence": 0.0, "wait_confidence": 0.0,
     "supports_setup": True, "contradictions": []},
    RuntimeError("connection refused"),              # service unavailable
])
def test_an_unusable_reply_abstains_and_keeps_the_baseline(reply):
    context, signals = scene()
    report = validate(signals, context, ScriptedClient([reply]))

    assert report.counts["abstain"] == 1
    pd.testing.assert_frame_equal(report.signals.target_weights, signals.target_weights)


def test_abstain_can_be_configured_to_fail_closed():
    context, signals = scene()
    report = validate(signals, context, ScriptedClient([RuntimeError("down")]),
                      config=ValidationConfig(on_abstain="drop"))

    assert report.counts["abstain"] == 1
    assert (report.signals.target_weights["AAA"] == 0).all()


def test_a_candidate_without_enough_history_abstains_without_calling_the_model():
    context, signals = scene(weights=np.concatenate([[0.1] * 3, np.zeros(BARS - 3)]))
    client = ScriptedClient([agreeing()])
    report = validate(signals, context, client)

    assert client.calls == [], "the model was asked to judge an empty window"
    assert report.counts["abstain"] == 1


def test_a_reply_slower_than_the_bar_expires_the_candidate():
    """Live, that order would have missed its fill."""
    context, signals = scene()
    slow = ScriptedClient([agreeing()], latency_s=999.0)
    report = validate(signals, context, slow, config=ValidationConfig(latency_budget_s=30.0))

    assert report.counts["expired"] == 1
    assert (report.signals.target_weights["AAA"] == 0).all()


def test_a_reply_inside_the_budget_is_honoured():
    context, signals = scene()
    quick = ScriptedClient([agreeing()], latency_s=8.0)
    report = validate(signals, context, quick, config=ValidationConfig(latency_budget_s=30.0))
    assert report.counts["keep"] == 1


def test_an_invalid_config_is_refused():
    with pytest.raises(ValueError):
        ValidationConfig(min_confidence=1.5)
    with pytest.raises(ValueError):
        ValidationConfig(on_abstain="maybe")


# ------------------------------------------------------------------- journal
def test_every_decision_is_journalled_with_what_produced_it(tmp_path):
    context, signals = scene()
    journal = Journal(tmp_path / "decisions.jsonl")
    validate(signals, context, ScriptedClient([agreeing()]), journal=journal)

    (row,) = journal.rows()
    assert {"key", "symbol", "timestamp", "direction", "prompt_version", "model",
            "snapshot", "verdict", "latency_s", "action"} <= set(row)
    assert row["action"] == Action.KEEP.value
    assert row["snapshot"]["indicators"]["momentum_z"] is not None


def test_a_journalled_decision_is_reused_instead_of_asked_again(tmp_path):
    context, signals = scene()
    journal = Journal(tmp_path / "decisions.jsonl")
    validate(signals, context, ScriptedClient([agreeing()]), journal=journal)

    second = ScriptedClient([agreeing(long=0.0)])   # would veto if it were called
    report = validate(signals, context, second, journal=journal)

    assert second.calls == [], "the model was called for a cached decision"
    assert report.counts["keep"] == 1 and report.counts["model_calls"] == 0


def test_the_cache_key_changes_when_the_prompt_or_model_does():
    context, signals = scene()
    (candidate,) = find_candidates(signals.target_weights)
    base = decision_key(candidate, prompt_version="v1", model="m")

    assert decision_key(candidate, prompt_version="v2", model="m") != base
    assert decision_key(candidate, prompt_version="v1", model="other") != base


def test_the_call_budget_stops_further_model_calls(tmp_path):
    column = np.zeros(BARS)
    for start in (20, 40, 60):
        column[start:start + 5] = 0.1
    context, signals = scene(weights=column)
    client = ScriptedClient([agreeing()])

    report = validate(signals, context, client, config=ValidationConfig(max_calls=2))
    assert len(client.calls) == 2 and report.counts["judged"] == 2


def test_the_judged_chart_is_kept_and_the_journal_points_at_it(tmp_path):
    """The image is part of the model's input, so a run without it is unauditable."""
    context, signals = scene()
    journal = Journal(tmp_path / "decisions.jsonl")
    images = tmp_path / "llm_inputs"

    validate(signals, context, ScriptedClient([agreeing()]), journal=journal,
             config=ValidationConfig(image_dir=str(images)))

    (row,) = journal.rows()
    kept = Path(row["image_path"])
    assert kept.exists() and kept.suffix == ".png" and kept.stat().st_size > 0
    assert row["symbol"] in kept.name, "the file must be findable from its row"


def test_no_image_is_kept_when_the_directory_is_unset(tmp_path):
    context, signals = scene()
    journal = Journal(tmp_path / "decisions.jsonl")
    validate(signals, context, ScriptedClient([agreeing()]), journal=journal)

    (row,) = journal.rows()
    assert row["image_path"] == ""


# ---------------------------------------------------- a missing model fails fast
def test_preflight_names_the_missing_model_and_what_is_served():
    """A wrong tag must stop a run, not become 20 abstentions."""
    from qtrader.decision.client import ModelUnavailable, OllamaClient

    class Served:
        def __init__(self, names):
            self.models = [type("M", (), {"model": n})() for n in names]

    client = OllamaClient("Qwen3.6")
    client._client = type("C", (), {"list": lambda self: Served(["Qwen3.6:27b-mlx"])})()

    with pytest.raises(ModelUnavailable, match="Qwen3.6:27b-mlx"):
        client.preflight()


def test_preflight_passes_when_the_model_is_served():
    from qtrader.decision.client import OllamaClient

    class Served:
        def __init__(self, names):
            self.models = [type("M", (), {"model": n})() for n in names]

    client = OllamaClient("Qwen3.6:27b-mlx")
    client._client = type("C", (), {"list": lambda self: Served(["Qwen3.6:27b-mlx"])})()
    client.preflight()
