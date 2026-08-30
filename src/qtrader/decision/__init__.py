"""LLM validation of quant candidates, between the strategy and the engine.

The layer never generates a trade. It receives entries the strategy already
decided on and may only *narrow* them: an entry it rejects goes flat, and
nothing replaces it. See ADR-0007 for why that constraint is load-bearing
rather than merely cautious.
"""

from .candidates import Candidate, find_candidates
from .schema import VERDICT_SCHEMA, Action, Verdict, decide
from .validator import ValidationConfig, validate

__all__ = [
    "Action",
    "Candidate",
    "ValidationConfig",
    "VERDICT_SCHEMA",
    "Verdict",
    "decide",
    "find_candidates",
    "validate",
]
