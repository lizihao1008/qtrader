"""Post-trade attribution: which trades worked, and what the setup looked like.

The reusable entry point is :func:`diagnose` — one call from a config (or an
already-executed run) to episodes, screens and a report, for any strategy::

    from qtrader.analysis import diagnose

    diagnosis = diagnose("config/backtest/my_strategy.yaml", split="mine")
    print(diagnosis.summary())
    diagnosis.save()
    diagnosis.write_report()
"""

from .attribution import Shortfall, diagnose_shortfall, performance_summary
from .conditions import (
    bonferroni_t_threshold,
    outcome_contrast,
    quantile_profile,
    rank_conditions,
)
from .diagnostics import Diagnosis, diagnose
from .episodes import Episodes, excursion_summary, extract_episodes, oriented_paths
from .features import FeatureSet, market_features, setup_features

__all__ = [
    "diagnose",
    "diagnose_shortfall",
    "performance_summary",
    "Shortfall",
    "Diagnosis",
    "Episodes",
    "extract_episodes",
    "oriented_paths",
    "excursion_summary",
    "FeatureSet",
    "market_features",
    "setup_features",
    "rank_conditions",
    "outcome_contrast",
    "quantile_profile",
    "bonferroni_t_threshold",
]
