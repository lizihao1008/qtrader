"""Which symbols a run may trade, and what each one is measured against.

A universe is three things:

* **symbols** — the tradable stocks;
* **benchmark** — the broad-market reference (SPY), used to strip market beta
  out of every stock's return;
* **sectors** — a map from each stock to its sector ETF, the V1 stand-in for a
  peer group (outline §7: static peers first, statistical peers later).

Membership is currently static. That is a documented simplification: a
historical study over a long window must make membership time-aware, or it
inherits survivorship bias. Over the intraday windows used today the constituent
list does not change, so the simplification is safe — but it must be revisited
before any multi-year backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Universe:
    """A named set of tradable symbols plus their market/sector references."""

    name: str
    symbols: tuple[str, ...]
    benchmark: str

    #: Optional — a single-symbol run has no peers to speak of. Strategies that
    #: need sectors call :meth:`require_sectors` and fail with a clear message.
    sectors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.sectors) - set(self.symbols)
        if unknown:
            raise ValueError(f"sector map mentions symbols outside the universe: {sorted(unknown)}")

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Universe":
        raw = yaml.safe_load(Path(path).read_text())
        return cls(
            name=raw.get("name", Path(path).stem),
            symbols=tuple(raw["symbols"]),
            benchmark=raw["benchmark"],
            sectors=dict(raw.get("sectors", {})),
        )

    def require_sectors(self) -> dict[str, str]:
        """Sector map, or a clear failure naming the symbols that lack one."""
        missing = sorted(set(self.symbols) - set(self.sectors))
        if missing:
            raise ValueError(
                f"universe {self.name!r} has no sector assigned to {missing}; "
                "this strategy needs a peer reference for every symbol"
            )
        return dict(self.sectors)

    @property
    def sector_etfs(self) -> tuple[str, ...]:
        """Distinct sector reference symbols, sorted."""
        return tuple(sorted(set(self.sectors.values())))

    @property
    def reference_symbols(self) -> tuple[str, ...]:
        """Benchmark + sector ETFs — needed as data, never traded."""
        return tuple(sorted({self.benchmark, *self.sector_etfs}))

    @property
    def all_symbols(self) -> tuple[str, ...]:
        """Everything a run must download."""
        return tuple(sorted({*self.symbols, *self.reference_symbols}))

    def with_symbol(self, symbol: str) -> "Universe":
        """A copy that also trades ``symbol``, leaving references unchanged."""
        if symbol in self.symbols:
            return self
        return replace(self, symbols=(*self.symbols, symbol))

    def peers(self, symbol: str) -> tuple[str, ...]:
        """Other universe members sharing ``symbol``'s sector."""
        sector = self.sectors[symbol]
        return tuple(s for s in self.symbols if s != symbol and self.sectors[s] == sector)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "symbols": list(self.symbols),
            "benchmark": self.benchmark,
            "sectors": dict(self.sectors),
        }
