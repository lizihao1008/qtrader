"""Research labels that may use future information.

Nothing in this package is a trading signal. A label at bar ``t`` is allowed to
look at ``t+1 ... t+H``; a live strategy is not. Keep that boundary explicit:
``qtrader.features`` stays causal, ``qtrader.labels`` does not.
"""

from .trend_events import (
    TrendDetectConfig,
    deduplicate_trend_events,
    detect_trend_events,
    score_trend_candidates,
)

__all__ = [
    "TrendDetectConfig",
    "score_trend_candidates",
    "deduplicate_trend_events",
    "detect_trend_events",
]
