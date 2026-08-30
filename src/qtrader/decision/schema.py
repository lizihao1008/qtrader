"""What the model is allowed to say, and what the system does with it.

Two things live here and they are deliberately separate:

* **the schema** the model's generation is constrained to — so a reply cannot be
  prose that a parser has to guess at;
* **the decision rule** that turns a verdict into an action — so the rule is one
  readable function with an explicit threshold, not logic scattered through a
  prompt.

The model reports a directional conviction for each side and for standing
aside. It never sees the position size and never influences it: `decide` returns
only KEEP or VETO, and the strategy's own sizing is untouched either way.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

LONG, SHORT = 1, -1

#: Passed to ollama as ``format=``. Generation is constrained to this, so the
#: reply is machine-readable by construction rather than by convention.
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "regime": {
            "type": "string",
            "enum": ["trend_up", "trend_down", "range", "volatile", "unclear"],
        },
        "long_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "short_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "wait_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "supports_setup": {"type": "boolean"},
        # Bounded on purpose. Uncapped free text is the whole generation budget:
        # measured 13.0 s/call uncapped against 3.7 s with these limits, and
        # below ~400 tokens an uncapped reply runs out mid-JSON and is unusable.
        # The fields that decide anything are numbers; this is for the audit
        # trail, so it only has to be readable.
        "contradictions": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 80},
        },
        "rationale": {"type": "string", "maxLength": 180},
    },
    "required": [
        "regime",
        "long_confidence",
        "short_confidence",
        "wait_confidence",
        "supports_setup",
        "contradictions",
    ],
}


class Action(str, enum.Enum):
    """What the layer did with a candidate. Journalled verbatim."""

    KEEP = "keep"          # the model agreed; the strategy's entry stands
    VETO = "veto"          # the model disagreed or was not confident enough
    ABSTAIN = "abstain"    # no usable verdict — see ValidationConfig.on_abstain
    EXPIRED = "expired"    # the reply would not have arrived before the fill


@dataclass(frozen=True)
class Verdict:
    """A parsed, validated model reply."""

    regime: str
    long_confidence: float
    short_confidence: float
    wait_confidence: float
    supports_setup: bool
    contradictions: tuple[str, ...] = ()
    rationale: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, payload: dict) -> "Verdict":
        """Build from a decoded reply, or raise if it does not honour the schema.

        The schema constrains generation, but a served model can still be
        swapped, downgraded or proxied, so the contract is checked here too.
        A violation is an abstention upstream, never a guess.
        """
        missing = [k for k in VERDICT_SCHEMA["required"] if k not in payload]
        if missing:
            raise ValueError(f"verdict missing required fields: {missing}")

        confidences = {}
        for name in ("long_confidence", "short_confidence", "wait_confidence"):
            value = payload[name]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be a number, got {value!r}")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must lie in [0, 1], got {value!r}")
            confidences[name] = float(value)

        regime = payload["regime"]
        if regime not in VERDICT_SCHEMA["properties"]["regime"]["enum"]:
            raise ValueError(f"unknown regime {regime!r}")
        if not isinstance(payload["supports_setup"], bool):
            raise ValueError("supports_setup must be a boolean")

        contradictions = payload.get("contradictions") or []
        if not isinstance(contradictions, list):
            raise ValueError("contradictions must be a list")

        return cls(
            regime=regime,
            supports_setup=payload["supports_setup"],
            contradictions=tuple(str(c) for c in contradictions),
            rationale=str(payload.get("rationale", "")),
            raw=dict(payload),
            **confidences,
        )

    def conviction(self, direction: int) -> float:
        """The model's confidence in the side the strategy wants to take."""
        return self.long_confidence if direction == LONG else self.short_confidence

    def as_record(self) -> dict:
        return {
            "regime": self.regime,
            "long_confidence": self.long_confidence,
            "short_confidence": self.short_confidence,
            "wait_confidence": self.wait_confidence,
            "supports_setup": self.supports_setup,
            "contradictions": list(self.contradictions),
            "rationale": self.rationale,
        }


def decide(verdict: Verdict, direction: int, *, min_confidence: float) -> Action:
    """KEEP only when the model points the same way as the quant, with conviction.

    Three conditions, all required:

    * the model's confidence in the strategy's own direction clears
      ``min_confidence``;
    * that side is the model's *preferred* side — a 0.7 long against a 0.8 short
      is not agreement, it is a model that mostly wants the other trade;
    * the model did not explicitly mark the setup unsupported.

    Anything else is a veto. There is no path from this function to a larger
    position, a different direction, or a trade the strategy did not propose.
    """
    mine = verdict.conviction(direction)
    theirs = verdict.conviction(-direction)

    if mine < min_confidence:
        return Action.VETO
    if mine <= theirs:
        return Action.VETO
    if not verdict.supports_setup:
        return Action.VETO
    return Action.KEEP
