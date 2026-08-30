"""The verdict contract and the rule that turns it into an action.

The rule is where the trading decision actually happens, so it is tested
exhaustively rather than by example: every path to a veto has its own case.
"""

from __future__ import annotations

import pytest

from qtrader.decision.schema import LONG, SHORT, Action, Verdict, decide


def verdict(long=0.8, short=0.1, wait=0.1, supports=True, regime="trend_up") -> Verdict:
    return Verdict.parse({
        "regime": regime, "long_confidence": long, "short_confidence": short,
        "wait_confidence": wait, "supports_setup": supports, "contradictions": [],
    })


# ------------------------------------------------------------------ parsing
def test_a_complete_reply_parses():
    v = verdict()
    assert (v.regime, v.long_confidence, v.supports_setup) == ("trend_up", 0.8, True)
    assert v.conviction(LONG) == 0.8 and v.conviction(SHORT) == 0.1


@pytest.mark.parametrize("field", [
    "regime", "long_confidence", "short_confidence", "wait_confidence", "supports_setup",
])
def test_a_missing_field_is_refused(field):
    payload = {"regime": "range", "long_confidence": 0.5, "short_confidence": 0.5,
               "wait_confidence": 0.0, "supports_setup": True, "contradictions": []}
    payload.pop(field)
    with pytest.raises(ValueError, match="missing required"):
        Verdict.parse(payload)


@pytest.mark.parametrize("value", [-0.1, 1.5, "0.8", None, True])
def test_a_confidence_outside_the_contract_is_refused(value):
    """A model that is swapped or proxied can still return anything."""
    with pytest.raises(ValueError):
        verdict(long=value)


def test_an_unknown_regime_is_refused():
    with pytest.raises(ValueError, match="unknown regime"):
        verdict(regime="euphoric")


# ------------------------------------------------------------------- the rule
def test_confident_agreement_keeps_the_entry():
    assert decide(verdict(long=0.75, short=0.1), LONG, min_confidence=0.6) is Action.KEEP


def test_confidence_below_the_threshold_vetoes():
    assert decide(verdict(long=0.59, short=0.1), LONG, min_confidence=0.6) is Action.VETO


def test_the_threshold_itself_passes():
    assert decide(verdict(long=0.6, short=0.1), LONG, min_confidence=0.6) is Action.KEEP


def test_disagreement_vetoes():
    """The model wants the other side; the strategy proposed LONG."""
    assert decide(verdict(long=0.1, short=0.9), LONG, min_confidence=0.6) is Action.VETO


def test_confidence_on_both_sides_is_not_agreement():
    """0.7 long is not support when the model likes short even more."""
    assert decide(verdict(long=0.7, short=0.8), LONG, min_confidence=0.6) is Action.VETO


def test_an_explicit_contradiction_vetoes_despite_confidence():
    assert decide(verdict(long=0.95, short=0.0, supports=False), LONG,
                  min_confidence=0.6) is Action.VETO


def test_the_short_side_is_judged_symmetrically():
    assert decide(verdict(long=0.1, short=0.8), SHORT, min_confidence=0.6) is Action.KEEP
    assert decide(verdict(long=0.8, short=0.1), SHORT, min_confidence=0.6) is Action.VETO


def test_the_rule_can_only_return_keep_or_veto():
    """It must never be able to invent or enlarge a trade."""
    for long in (0.0, 0.3, 0.6, 1.0):
        for short in (0.0, 0.3, 0.6, 1.0):
            for supports in (True, False):
                for direction in (LONG, SHORT):
                    action = decide(verdict(long=long, short=short, supports=supports),
                                    direction, min_confidence=0.6)
                    assert action in (Action.KEEP, Action.VETO)
