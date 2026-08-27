"""Strategy lookup by name, so runs are fully specified by a config file."""

from __future__ import annotations

from .base import Strategy
from .cross_sectional import CrossSectionalResidualStrategy
from .ma_cross import MACrossStrategy
from .trend_ratchet import TrendRatchetStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    MACrossStrategy.name: MACrossStrategy,
    CrossSectionalResidualStrategy.name: CrossSectionalResidualStrategy,
    TrendRatchetStrategy.name: TrendRatchetStrategy,
}


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(STRATEGIES)}")
    return STRATEGIES[name](**(params or {}))
