"""A durable record of every hypothesis tested, in the order it was tested.

A research loop that keeps only its best result is indistinguishable from one
that got lucky. The ledger keeps all of them — including the ones that failed,
especially the ones that failed — so that afterwards it is possible to ask how
many things were tried before something looked good. That number is the single
most important input when judging whether a surviving result means anything:
twenty hypotheses and one winner at t=2 is not a finding, it is arithmetic.

One line per trial, appended, never rewritten.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

DEFAULT_LEDGER = Path("results/search/ledger.jsonl")


@dataclass(frozen=True)
class Trial:
    """One tested hypothesis and what it produced."""

    label: str
    hypothesis: str
    split: str
    overrides: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    verdict: str = ""
    recorded_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )


def append_trial(trial: Trial, path: Path | str = DEFAULT_LEDGER) -> Path:
    """Append one trial. The file is never rewritten, only added to."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(asdict(trial), default=str) + "\n")
    return path


def read_ledger(path: Path | str = DEFAULT_LEDGER) -> pd.DataFrame:
    """Every trial so far, flattened, in the order it was run."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()

    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return pd.DataFrame()

    flat = []
    for row in rows:
        record = {k: v for k, v in row.items() if k not in ("metrics", "overrides")}
        record.update(row.get("metrics", {}))
        record["overrides"] = json.dumps(row.get("overrides", {}), sort_keys=True)
        flat.append(record)
    return pd.DataFrame(flat)


def trials_on(split: str, path: Path | str = DEFAULT_LEDGER) -> int:
    """How many hypotheses have been tested against one window.

    The multiple-testing count for that window. A result found after ``n`` tries
    needs to clear a bar that rises with ``n``.
    """
    ledger = read_ledger(path)
    if ledger.empty:
        return 0
    return int((ledger["split"] == split).sum())
