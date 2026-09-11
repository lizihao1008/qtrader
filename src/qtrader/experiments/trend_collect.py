"""Offline collection of trend-start events for visual audit.

Loads 1-minute bars (no panel forward-fill), scores every symbol independently,
writes candidates / events / summary, and builds the chart folders a person
flips through to ask "does this label look like a trend?"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.alpaca_client import resolve_symbol
from ..data.ingest import ensure_bars, load_clean_bars, to_utc
from ..data.schema import validate_bars
from ..data.sessions import session_date
from ..data.storage import BarStore
from ..labels.trend_events import TrendDetectConfig, deduplicate_trend_events, score_trend_candidates
from ..viz.trend_events import (
    save_event_chart,
    write_trend_overview,
)

QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)


@dataclass(frozen=True)
class TrendCollectConfig:
    """Notebook-facing knobs: what to load, how to label, what to draw."""

    symbols: tuple[str, ...] = ("QQQ",)
    start: str = "2026-01-02"
    end: str = "2026-09-09"
    feed: str = "iex"
    timeframe: str = "1Min"
    detect: TrendDetectConfig = field(default_factory=TrendDetectConfig)
    output_dir: str = "results/trend_events"
    pre_window: int = 40
    post_extra: int = 10
    gallery_n: int = 10
    overview_per_side: int = 6
    random_seed: int = 0
    borderline_z_max: float = 1.8
    download_if_missing: bool = True

    def with_detect(self, **overrides) -> "TrendCollectConfig":
        return replace(self, detect=replace(self.detect, **overrides))


@dataclass
class TrendCollectResult:
    config: TrendCollectConfig
    bars: dict[str, pd.DataFrame]
    candidates: pd.DataFrame
    events: pd.DataFrame
    summary: dict
    output_dir: Path

    def strongest(self, n: int | None = None) -> pd.DataFrame:
        return _take_ranked(self.events, n or self.config.gallery_n)

    def random_sample(self, n: int | None = None) -> pd.DataFrame:
        return _take_random(
            self.events, n or self.config.gallery_n, self.config.random_seed
        )

    def borderline(self, n: int | None = None) -> pd.DataFrame:
        z = self.events["ztrend"].abs()
        band = self.events.loc[
            (z >= self.config.detect.z_threshold) & (z <= self.config.borderline_z_max)
        ]
        return _take_ranked(band, n or self.config.gallery_n)


def collect_trend_events(config: TrendCollectConfig) -> TrendCollectResult:
    """Download if needed, score, dedup, write artifacts, return the tables."""
    symbols = tuple(resolve_symbol(s) for s in config.symbols)
    start = to_utc(config.start)
    end = to_utc(config.end)
    store = BarStore()
    if config.download_if_missing:
        ensure_bars(
            symbols, start, end,
            timeframe=config.timeframe, feed=config.feed, store=store,
            long_lookback_for=symbols,
        )

    bars: dict[str, pd.DataFrame] = {}
    candidate_parts = []
    event_parts = []
    n_valid = 0
    for symbol in symbols:
        frame = load_clean_bars(
            symbol, timeframe=config.timeframe, feed=config.feed,
            start=start, end=end, store=store,
        )
        if frame.empty:
            raise ValueError(f"no clean 1-minute bars for {symbol} in [{config.start}, {config.end})")
        validate_bars(frame, symbol, expected_interval=config.detect.bar_delta, strict=True)
        bars[symbol] = frame
        scored = score_trend_candidates(frame, symbol, config.detect)
        n_valid += int((scored["valid_sigma"] & scored["valid_future"]).sum())
        candidate_parts.append(scored.loc[scored["candidate_direction"] != 0].copy())
        event_parts.append(deduplicate_trend_events(scored, config.detect))

    candidates = pd.concat(candidate_parts, ignore_index=True) if candidate_parts else pd.DataFrame()
    events = pd.concat(event_parts, ignore_index=True) if event_parts else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["symbol", "bar_open"]).reset_index(drop=True)

    n_bars = sum(len(frame) for frame in bars.values())
    summary = build_summary(config, bars, candidates, events, n_bars, n_valid)

    output_dir = Path(config.output_dir)
    _write_tables(output_dir, candidates, events, summary)
    _write_galleries(output_dir, config, bars, events)

    return TrendCollectResult(
        config=config, bars=bars, candidates=candidates, events=events,
        summary=summary, output_dir=output_dir,
    )


def build_summary(
    config: TrendCollectConfig,
    bars: dict[str, pd.DataFrame],
    candidates: pd.DataFrame,
    events: pd.DataFrame,
    n_bars: int,
    n_valid: int,
) -> dict:
    up_c = int((candidates["candidate_direction"] == 1).sum()) if not candidates.empty else 0
    down_c = int((candidates["candidate_direction"] == -1).sum()) if not candidates.empty else 0
    up_e = int((events["direction"] == 1).sum()) if not events.empty else 0
    down_e = int((events["direction"] == -1).sum()) if not events.empty else 0
    by_symbol = {
        str(symbol): {
            "events": int(len(group)),
            "up": int((group["direction"] == 1).sum()),
            "down": int((group["direction"] == -1).sum()),
        }
        for symbol, group in events.groupby("symbol")
    } if not events.empty else {}
    per_day = _events_per_day(events)
    return {
        "config": {"symbols": list(config.symbols), **config.detect.to_dict()},
        "n_bars": n_bars,
        "n_valid_bars": n_valid,
        "candidates": {"up": up_c, "down": down_c, "total": up_c + down_c},
        "events": {"up": up_e, "down": down_e, "total": up_e + down_e},
        "event_rate_of_valid_bars": (up_e + down_e) / n_valid if n_valid else 0.0,
        "ztrend": _describe(events["ztrend"]) if not events.empty else {},
        "ER": _describe(events["ER"]) if not events.empty else {},
        "MAE_norm": _describe(events["MAE_norm"]) if not events.empty else {},
        "MFE_norm": _describe(events["MFE_norm"]) if not events.empty else {},
        "candidate_run_length": _describe(events["candidate_run_length"]) if not events.empty else {},
        "events_per_day": per_day,
        "by_symbol": by_symbol,
        "note": (
            "These labels look into the future H-minute window. "
            "They are not a live trading signal."
        ),
    }


def summary_text(summary: dict) -> str:
    ev = summary["events"]
    cand = summary["candidates"]
    lines = [
        "Trend event detector — V1 heuristic labels (use future data)",
        f"bars: {summary['n_bars']:,}   valid (sigma + future window): {summary['n_valid_bars']:,}",
        f"candidates: UP {cand['up']:,}  DOWN {cand['down']:,}",
        f"events:     UP {ev['up']:,}  DOWN {ev['down']:,}  "
        f"rate {summary['event_rate_of_valid_bars']:.4%} of valid bars",
        f"events/day: mean={summary['events_per_day'].get('mean', float('nan')):.2f}  "
        f"median={summary['events_per_day'].get('median', float('nan')):.2f}",
        "",
        _block("ZTrend", summary["ztrend"]),
        _block("ER", summary["ER"]),
        _block("MAE_norm", summary["MAE_norm"]),
        _block("MFE_norm", summary["MFE_norm"]),
        _block("candidate_run_length", summary["candidate_run_length"]),
        "by symbol: " + json.dumps(summary["by_symbol"], default=str),
        "",
        summary["note"],
    ]
    return "\n".join(lines) + "\n"


def _write_tables(output_dir: Path, candidates: pd.DataFrame, events: pd.DataFrame, summary: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _dump(output_dir / "trend_candidates", candidates)
    _dump(output_dir / "trend_events", events)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (output_dir / "summary.txt").write_text(summary_text(summary))


def _write_galleries(
    output_dir: Path,
    config: TrendCollectConfig,
    bars: dict[str, pd.DataFrame],
    events: pd.DataFrame,
) -> None:
    result = TrendCollectResult(
        config=config, bars=bars, candidates=pd.DataFrame(), events=events,
        summary={}, output_dir=output_dir,
    )
    folders = {
        "strongest": result.strongest(),
        "random": result.random_sample(),
        "borderline": result.borderline(),
    }
    for name, sample in folders.items():
        folder = output_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for _, event in sample.iterrows():
            save_event_chart(
                bars[event["symbol"]], event, folder,
                pre_window=config.pre_window,
                post_extra=config.post_extra,
            )
    up = events.loc[events["direction"] == 1] if not events.empty else events
    down = events.loc[events["direction"] == -1] if not events.empty else events
    overview_up = _take_random(up, config.overview_per_side, config.random_seed)
    overview_down = _take_random(down, config.overview_per_side, config.random_seed + 1)
    write_trend_overview(
        output_dir / "trend_overview.html",
        bars,
        overview_up,
        overview_down,
        pre_window=config.pre_window,
        post_extra=config.post_extra,
    )


def _dump(stem: Path, frame: pd.DataFrame) -> None:
    frame.to_parquet(stem.with_suffix(".parquet"), index=False)
    frame.to_csv(stem.with_suffix(".csv"), index=False)


def _take_ranked(events: pd.DataFrame, n: int) -> pd.DataFrame:
    if events.empty:
        return events
    return events.assign(_abs=events["ztrend"].abs()).nlargest(n, "_abs").drop(columns="_abs")


def _take_random(events: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    if events.empty or n <= 0:
        return events.iloc[0:0]
    take = min(n, len(events))
    return events.sample(n=take, random_state=seed).sort_values(["symbol", "bar_open"])


def _describe(series: pd.Series) -> dict:
    clean = series.dropna().astype(float)
    if clean.empty:
        return {}
    q = {f"q{int(p * 100)}": float(clean.quantile(p)) for p in QUANTILES}
    return {
        "count": int(clean.shape[0]),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "std": float(clean.std(ddof=1)) if len(clean) > 1 else 0.0,
        **q,
    }


def _events_per_day(events: pd.DataFrame) -> dict:
    if events.empty:
        return {"mean": 0.0, "median": 0.0, "days": 0}
    days = session_date(pd.DatetimeIndex(events["bar_open"]))
    counts = events.groupby(days.to_numpy()).size()
    return {
        "mean": float(counts.mean()),
        "median": float(counts.median()),
        "days": int(len(counts)),
    }


def _block(name: str, stats: dict) -> str:
    if not stats:
        return f"{name}: (none)"
    return (
        f"{name}: mean={stats['mean']:.3f}  median={stats['median']:.3f}  "
        f"std={stats.get('std', float('nan')):.3f}  "
        + "  ".join(f"{k}={stats[k]:.3f}" for k in stats if k.startswith("q"))
    )
