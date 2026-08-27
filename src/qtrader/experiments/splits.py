"""Chronological evaluation splits.

CLAUDE.md §11.3: never shuffle time-series observations across train and test.
Splits are contiguous date windows declared in one YAML file, fixed *before*
results are inspected, so the boundary cannot quietly move to wherever the
answer looks better.

A split also records **why** it exists. That matters more than the dates: a
window whose parameters were chosen by looking at it is not a test set, however
late in the calendar it sits, and labelling it honestly is the only thing that
stops it being cited as evidence six weeks later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from ..config import RunConfig

DEFAULT_SPLITS_PATH = Path("config/splits.yaml")


@dataclass(frozen=True)
class Split:
    """One contiguous evaluation window."""

    name: str
    start: str
    end: str
    purpose: str = ""

    def describe(self) -> str:
        return f"{self.name}: {self.start} -> {self.end}"


def load_splits(path: Path | str = DEFAULT_SPLITS_PATH) -> dict[str, Split]:
    raw = yaml.safe_load(Path(path).read_text())["splits"]
    return {
        name: Split(name=name, start=str(body["start"]), end=str(body["end"]),
                    purpose=body.get("purpose", "").strip())
        for name, body in raw.items()
    }


def apply_split(config: RunConfig, split: Split) -> RunConfig:
    """A copy of ``config`` restricted to the split's window.

    The ``run_id`` is suffixed so results from different splits never overwrite
    each other — losing a validation run to a filename collision is a silent
    way to end up reporting the training number twice.
    """
    return replace(
        config,
        run_id=f"{config.run_id}__{split.name}",
        data=replace(config.data, start=split.start, end=split.end),
    )
