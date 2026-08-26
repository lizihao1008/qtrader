"""Local dataset storage.

Logical layers on disk (see docs/context/CONTEXT.md):

    data/raw/bars/{feed}/{timeframe}/{symbol}.parquet     exactly as received
    data/clean/bars/{feed}/{timeframe}/{symbol}.parquet   validated, RTH-filtered

Raw is immutable. Re-downloading an overlapping window is allowed and merges
into the existing file, but if the provider now reports *different* values for
a timestamp already stored, :meth:`BarStore.write_raw` refuses the write rather
than silently rewriting history.

Each parquet file has a ``.json`` sidecar recording how it was produced, so a
dataset can always be traced back to provider, feed and fetch time.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from .schema import coerce_bars

DEFAULT_DATA_ROOT = Path("data")


class RawDataConflict(RuntimeError):
    """Raised when a raw write would change already-stored values."""


@dataclass(frozen=True)
class DatasetKey:
    """Identifies one stored bar dataset."""

    symbol: str
    timeframe: str
    feed: str

    def relative_path(self, layer: str) -> Path:
        return Path(layer) / "bars" / self.feed / self.timeframe / f"{self.symbol}.parquet"


class BarStore:
    """Read/write bar datasets under a data root directory."""

    def __init__(self, root: Path | str = DEFAULT_DATA_ROOT):
        self.root = Path(root)

    # ------------------------------------------------------------------ paths
    def path(self, key: DatasetKey, layer: str) -> Path:
        return self.root / key.relative_path(layer)

    def exists(self, key: DatasetKey, layer: str) -> bool:
        return self.path(key, layer).exists()

    # ------------------------------------------------------------------- read
    def read(self, key: DatasetKey, layer: str) -> pd.DataFrame:
        path = self.path(key, layer)
        if not path.exists():
            raise FileNotFoundError(
                f"no {layer} dataset for {key.symbol} {key.timeframe} ({key.feed}) at {path}"
            )
        return coerce_bars(pd.read_parquet(path))

    def read_manifest(self, key: DatasetKey, layer: str) -> dict:
        path = self.path(key, layer).with_suffix(".json")
        return json.loads(path.read_text()) if path.exists() else {}

    # ------------------------------------------------------------------ write
    def write_raw(self, key: DatasetKey, bars: pd.DataFrame, **manifest_extra) -> Path:
        """Merge ``bars`` into the raw layer, refusing to alter stored values."""
        bars = coerce_bars(bars)
        path = self.path(key, "raw")

        if path.exists():
            stored = self.read(key, "raw")
            overlap = stored.index.intersection(bars.index)
            if len(overlap):
                a = stored.loc[overlap, list(stored.columns)]
                b = bars.loc[overlap, list(stored.columns)]
                if not a.round(6).equals(b.round(6)):
                    raise RawDataConflict(
                        f"provider values changed for {len(overlap)} stored timestamps "
                        f"of {key.symbol}; raw data is immutable — inspect {path} manually"
                    )
            bars = pd.concat([stored, bars.loc[bars.index.difference(stored.index)]])
            bars = bars.sort_index()

        return self._write(path, key, bars, layer="raw", **manifest_extra)

    def write_clean(self, key: DatasetKey, bars: pd.DataFrame, **manifest_extra) -> Path:
        """Overwrite the clean layer — it is always reproducible from raw."""
        return self._write(self.path(key, "clean"), key, coerce_bars(bars), layer="clean", **manifest_extra)

    def _write(self, path: Path, key: DatasetKey, bars: pd.DataFrame, *, layer: str, **extra) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        bars.to_parquet(path)

        manifest = {
            **asdict(key),
            "layer": layer,
            "provider": "alpaca",
            "n_rows": int(len(bars)),
            "start": str(bars.index.min()) if len(bars) else None,
            "end": str(bars.index.max()) if len(bars) else None,
            "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            **extra,
        }
        path.with_suffix(".json").write_text(json.dumps(manifest, indent=2, default=str))
        return path
