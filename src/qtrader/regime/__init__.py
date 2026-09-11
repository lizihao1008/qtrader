"""Online market-state detection.

This package estimates the *current* regime of a 1-minute price path. It does
not forecast. Every quantity at bar ``t`` uses bars ``<= t`` only: Kalman
*filter* (never a smoother), causal rolling variance, causal ER, CUSUM.

The first detector is :class:`KalmanCUSUMRegimeDetector`. Evaluation helpers in
:mod:`qtrader.regime.evaluate` may use future-looking labels; those labels never
enter the filter.
"""

from .config import KalmanCUSUMConfig, PRESETS, default_config
from .cusum import update_cusum
from .detector import KalmanCUSUMRegimeDetector, replay_symbol
from .evaluate import delay_far_grid, describe_entries, evaluate_regime, null_entry_rate
from .kalman import FilterStep, LocalLinearTrendFilter, process_noise_covariance

__all__ = [
    "FilterStep",
    "KalmanCUSUMConfig",
    "KalmanCUSUMRegimeDetector",
    "LocalLinearTrendFilter",
    "PRESETS",
    "default_config",
    "delay_far_grid",
    "describe_entries",
    "evaluate_regime",
    "null_entry_rate",
    "process_noise_covariance",
    "replay_symbol",
    "update_cusum",
]
