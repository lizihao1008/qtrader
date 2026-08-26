"""Run configuration.

A run is fully specified by one YAML file (CLAUDE.md §14: configuration is
externalised, never hard-coded). The same file names the universe, the data
window, the tradability rules, the strategy, the cost assumptions and the
execution rules, and is copied verbatim into the run manifest so a result can
always be reproduced.

Secrets are *not* part of the config — API credentials come from the
environment only.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .backtest.costs import CostModel
from .backtest.engine import ExecutionConfig
from .universe.definition import Universe
from .universe.filters import LiquidityFilter


@dataclass(frozen=True)
class DataConfig:
    """Which dataset window a run consumes. *Which* symbols comes from the universe."""

    start: str
    end: str
    timeframe: str = "1Min"
    feed: str = "iex"
    regular_hours_only: bool = True

    def start_dt(self) -> dt.datetime:
        return _parse_datetime(self.start)

    def end_dt(self) -> dt.datetime:
        return _parse_datetime(self.end)


@dataclass(frozen=True)
class StrategyConfig:
    """Which strategy, with which parameters."""

    name: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    """Everything one backtest run needs."""

    run_id: str
    universe_path: str
    data: DataConfig
    strategy: StrategyConfig
    liquidity: LiquidityFilter = field(default_factory=LiquidityFilter)
    costs: CostModel = field(default_factory=CostModel)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    data_root: str = "data"
    results_root: str = "results"

    #: Symbols to draw K-line charts for. Empty means "the most-traded symbol".
    report_symbols: tuple[str, ...] = ()

    @classmethod
    def from_yaml(cls, path: Path | str) -> "RunConfig":
        path = Path(path)
        raw = yaml.safe_load(path.read_text()) or {}
        return cls(
            run_id=raw.get("run_id", path.stem),
            universe_path=raw["universe"],
            data=DataConfig(**raw["data"]),
            strategy=StrategyConfig(**raw["strategy"]),
            liquidity=LiquidityFilter(**raw.get("liquidity", {})),
            costs=CostModel(**raw.get("costs", {})),
            execution=ExecutionConfig(**raw.get("execution", {})),
            data_root=raw.get("data_root", "data"),
            results_root=raw.get("results_root", "results"),
            report_symbols=tuple(raw.get("report", {}).get("symbols", [])),
        )

    def universe(self) -> Universe:
        return Universe.from_yaml(self.universe_path)

    def to_dict(self) -> dict:
        """Plain-data view used by the run manifest."""
        return {
            "run_id": self.run_id,
            "universe": self.universe_path,
            "data": _as_dict(self.data),
            "strategy": {"name": self.strategy.name, "params": dict(self.strategy.params)},
            "liquidity": _as_dict(self.liquidity),
            "costs": _as_dict(self.costs),
            "execution": _as_dict(self.execution),
            "data_root": self.data_root,
            "results_root": self.results_root,
            "report_symbols": list(self.report_symbols),
        }

    def output_dir(self) -> Path:
        return Path(self.results_root) / self.run_id


def _as_dict(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _parse_datetime(value: str | dt.datetime) -> dt.datetime:
    """Parse a config date/datetime; naive values are interpreted as UTC."""
    moment = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
    return moment.replace(tzinfo=dt.timezone.utc) if moment.tzinfo is None else moment
