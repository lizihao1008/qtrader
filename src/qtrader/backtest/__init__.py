"""Cost-aware simulation: cost model, portfolio accounting, engine, metrics."""

from .costs import CostModel
from .engine import BacktestEngine, BacktestResult, ExecutionConfig

__all__ = ["CostModel", "BacktestEngine", "BacktestResult", "ExecutionConfig"]
