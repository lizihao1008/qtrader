"""An append-only record of every decision, sufficient to replay it.

One JSONL row per candidate, carrying the candidate, the feature snapshot, the
prompt and its version, the model and its options, the raw reply, the parsed
verdict, the latency and the action taken. The brief asks for all of it; the
practical reason is that a run costing hours of inference must never need to be
repeated to answer a question about itself.

The same file is the cache. A row is keyed by the content of what produced it —
candidate, prompt version, model — so re-running with an unchanged prompt and
model reuses the recorded verdicts and costs nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


def decision_key(candidate, *, prompt_version: str, model: str) -> str:
    """Stable identity of one decision, so a rerun can find it again."""
    parts = (
        candidate.symbol,
        candidate.timestamp.isoformat(),
        str(candidate.direction),
        prompt_version,
        model,
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass
class Journal:
    """Append-only JSONL, with the cache read from the same rows."""

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict]:
        """Recorded decisions by key. A truncated final row is ignored, not fatal."""
        if not self.path.exists():
            return {}
        recorded: dict[str, dict] = {}
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a run killed mid-write; the rest of the file is good
            if "key" in row:
                recorded[row["key"]] = row
        return recorded

    def append(self, row: dict) -> None:
        with self.path.open("a") as handle:
            handle.write(json.dumps(row, default=str) + "\n")

    def rows(self) -> list[dict]:
        return list(self.load().values())
