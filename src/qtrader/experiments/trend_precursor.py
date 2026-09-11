"""Trend precursor test: is pre-event momentum different from ordinary minutes?

Not a strategy and not a search. Every factor ends strictly before the event
bar. Controls are same-symbol, similar time-of-day, and at least
``exclusion_minutes`` away from every Trend Start.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.ingest import load_clean_bars
from ..data.schema import coerce_bars, validate_bars
from ..data.sessions import session_date, to_market_time
from ..features.lookback import log_momentum
from ..viz.trend_precursor import write_precursor_charts, _decile_bucket

MOM_WINDOWS = (1, 3, 5, 10, 20)
LEADS = (1, 3, 5, 10)
EVENT_TIME_LAGS = tuple(range(-30, 0))


@dataclass(frozen=True)
class PrecursorConfig:
    """Notebook knobs. None of these are fitted."""

    events_path: str = "results/trend_events/trend_events.csv"
    output_dir: str = "results/trend_precursor"
    timeframe: str = "1Min"
    feed: str = "iex"
    mom_windows: tuple[int, ...] = MOM_WINDOWS
    leads: tuple[int, ...] = LEADS
    controls_per_event: int = 5
    tod_tolerance_minutes: int = 30
    exclusion_minutes: int = 30
    event_time_pre: int = 30
    random_seed: int = 42
    n_bootstrap: int = 500
    bar_minutes: int = 1


@dataclass
class PrecursorResult:
    config: PrecursorConfig
    samples: pd.DataFrame
    stats: pd.DataFrame
    event_time: pd.DataFrame
    summary: dict
    output_dir: Path


def run_precursor_test(config: PrecursorConfig | None = None) -> PrecursorResult:
    """Load events + bars, match controls, test, write artifacts."""
    config = config or PrecursorConfig()
    events = _load_events(config.events_path)
    bars = _load_bars(events, config)
    samples = build_precursor_samples(events, bars, config)
    stats = precursor_statistics(samples, config)
    event_time = event_time_curves(samples, bars, config)
    stats = _attach_bootstrap(stats, samples, config)
    summary = build_summary(samples, stats, event_time, config)
    output_dir = Path(config.output_dir)
    _write_artifacts(output_dir, samples, stats, summary)
    write_precursor_charts(
        output_dir, samples, stats, event_time,
        windows=config.mom_windows, leads=config.leads,
    )
    (output_dir / "summary.txt").write_text(_summary_text(summary))
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return PrecursorResult(
        config=config, samples=samples, stats=stats,
        event_time=event_time, summary=summary, output_dir=output_dir,
    )


def build_precursor_samples(
    events: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: PrecursorConfig,
) -> pd.DataFrame:
    """One row per trend or matched control. Factors end before the event bar."""
    rng = np.random.default_rng(config.random_seed)
    rows = []
    used: dict[str, set] = {symbol: set() for symbol in bars}
    events = events.sort_values(["symbol", "bar_open"]).reset_index(drop=True)
    for event_id, event in events.iterrows():
        symbol = str(event["symbol"])
        frame = bars[symbol]
        moms = _momentum_table(frame["close"], config.mom_windows, config.bar_minutes)
        eligible = _eligible_controls(
            frame.index, events.loc[events["symbol"] == symbol, "bar_open"],
            pd.Timestamp(event["bar_open"]), config, used=used[symbol],
        )
        take = min(config.controls_per_event, int(eligible.sum()))
        picks = (
            rng.choice(frame.index[eligible].to_numpy(), size=take, replace=False)
            if take else []
        )
        used[symbol].update(pd.DatetimeIndex(picks))
        rows.append(_sample_row(
            event_id=int(event_id), sample_type="trend", is_trend=1,
            symbol=symbol, bar_open=pd.Timestamp(event["bar_open"]),
            trend_start=pd.Timestamp(event["trend_start"]),
            direction=int(event["direction"]),
            ztrend=float(event["ztrend"]), er=float(event["ER"]),
            close=frame["close"], moms=moms, config=config,
        ))
        for stamp in picks:
            rows.append(_sample_row(
                event_id=int(event_id), sample_type="control", is_trend=0,
                symbol=symbol, bar_open=pd.Timestamp(stamp),
                trend_start=pd.NaT, direction=int(event["direction"]),
                ztrend=np.nan, er=np.nan,
                close=frame["close"], moms=moms, config=config,
            ))
    samples = pd.DataFrame(rows)
    _assert_no_leakage(samples)
    return samples


def precursor_statistics(samples: pd.DataFrame, config: PrecursorConfig) -> pd.DataFrame:
    """Trend vs control for every window × lead. No model, no search."""
    rows = []
    for window in config.mom_windows:
        for lead in config.leads:
            raw = f"mom_{window}_lead{lead}"
            rows.append(_one_test(samples, raw, window, lead, "up", +1, signed=False))
            rows.append(_one_test(samples, raw, window, lead, "down", -1, signed=False))
            rows.append(_one_test(
                samples, f"dmom_{window}_lead{lead}", window, lead,
                "directional", None, signed=True,
            ))
    return pd.DataFrame(rows)


def event_time_curves(
    samples: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: PrecursorConfig,
) -> pd.DataFrame:
    """Mean mom_5 at t-30 … t-1 for trend vs control, by side."""
    mom5 = {
        symbol: log_momentum(frame["close"], 5, bar_minutes=config.bar_minutes)
        for symbol, frame in bars.items()
    }
    rows = []
    for side, mask in (
        ("up", samples["direction"] == 1),
        ("down", samples["direction"] == -1),
        ("directional", pd.Series(True, index=samples.index)),
    ):
        block = samples.loc[mask]
        for kind in ("trend", "control"):
            part = block.loc[block["sample_type"] == kind]
            for lag in range(-config.event_time_pre, 0):
                values = []
                for _, row in part.iterrows():
                    stamp = pd.Timestamp(row["bar_open"]) + pd.Timedelta(minutes=lag)
                    series = mom5[row["symbol"]]
                    if stamp not in series.index:
                        continue
                    value = float(series.loc[stamp])
                    if not np.isfinite(value):
                        continue
                    if side == "directional":
                        value *= int(row["direction"])
                    values.append(value)
                arr = np.asarray(values, dtype=float)
                mean = float(np.nanmean(arr)) if len(arr) else np.nan
                se = float(np.nanstd(arr, ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else np.nan
                rows.append(
                    {
                        "side": side, "sample_type": kind, "lag": lag,
                        "n": int(np.isfinite(arr).sum()) if len(arr) else 0,
                        "mean": mean,
                        "lo": mean - 1.96 * se if np.isfinite(se) else np.nan,
                        "hi": mean + 1.96 * se if np.isfinite(se) else np.nan,
                    }
                )
    return pd.DataFrame(rows)


def build_summary(
    samples: pd.DataFrame,
    stats: pd.DataFrame,
    event_time: pd.DataFrame,
    config: PrecursorConfig,
) -> dict:
    directional = stats.loc[stats["side"] == "directional"].copy()
    n_trend = int((samples["sample_type"] == "trend").sum())
    n_control = int((samples["sample_type"] == "control").sum())
    n_up = int(((samples["sample_type"] == "trend") & (samples["direction"] == 1)).sum())
    n_down = int(((samples["sample_type"] == "trend") & (samples["direction"] == -1)).sum())
    n_days = int(samples["session_date"].nunique())
    best_display = directional.sort_values("auc", ascending=False).head(5)
    auc_ci_excludes_half = bool(
        ((directional["auc_ci_lo"] > 0.5) | (directional["auc_ci_hi"] < 0.5)).any()
    ) if "auc_ci_lo" in directional.columns else False
    mean_diff_supported = bool(
        ((directional["diff_ci_lo"] > 0) | (directional["diff_ci_hi"] < 0)).any()
    ) if "diff_ci_lo" in directional.columns else False
    top_lift = directional["top_decile_lift"]
    return {
        "n_trend": n_trend,
        "n_control": n_control,
        "n_up": n_up,
        "n_down": n_down,
        "n_days": n_days,
        "baseline_p_trend": n_trend / max(n_trend + n_control, 1),
        "controls_per_event_mean": n_control / max(n_trend, 1),
        "auc_min": float(directional["auc"].min()) if not directional.empty else np.nan,
        "auc_max": float(directional["auc"].max()) if not directional.empty else np.nan,
        "auc_median": float(directional["auc"].median()) if not directional.empty else np.nan,
        "highest_auc_cells": best_display[
            ["window", "lead", "auc", "mean_diff", "top_decile_lift"]
        ].to_dict(orient="records"),
        "any_auc_ci_excludes_0.5": auc_ci_excludes_half,
        "any_mean_diff_ci_excludes_0": mean_diff_supported,
        "top_decile_lift_median": float(top_lift.median()) if not top_lift.empty else np.nan,
        "sample_too_small": n_trend < 30 or n_days < 10,
        "note": (
            "Display only — do not treat the highest-AUC cell as a selected parameter. "
            "Labels look into the future; this test asks whether the past looked unusual."
        ),
        "config": {
            "events_path": config.events_path,
            "mom_windows": list(config.mom_windows),
            "leads": list(config.leads),
            "exclusion_minutes": config.exclusion_minutes,
            "tod_tolerance_minutes": config.tod_tolerance_minutes,
            "random_seed": config.random_seed,
            "n_bootstrap": config.n_bootstrap,
        },
    }


def _load_events(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"no trend events at {path}; run trend_collect.ipynb first"
        )
    events = pd.read_csv(path) if path.suffix == ".csv" else pd.read_parquet(path)
    need = {"symbol", "trend_start", "direction", "ztrend", "ER"}
    missing = need - set(events.columns)
    if missing:
        raise ValueError(f"events table missing columns: {sorted(missing)}")
    events["trend_start"] = pd.to_datetime(events["trend_start"], utc=True)
    if "bar_open" in events.columns:
        events["bar_open"] = pd.to_datetime(events["bar_open"], utc=True)
    else:
        events["bar_open"] = events["trend_start"] - pd.Timedelta(minutes=1)
    events["direction"] = events["direction"].astype(int)
    return events.reset_index(drop=True)


def _load_bars(events: pd.DataFrame, config: PrecursorConfig) -> dict[str, pd.DataFrame]:
    extra = pd.Timedelta(days=2)
    start = events["bar_open"].min() - extra
    end = events["bar_open"].max() + pd.Timedelta(days=1)
    bars = {}
    for symbol in sorted(events["symbol"].unique()):
        frame = load_clean_bars(
            symbol, timeframe=config.timeframe, feed=config.feed,
            start=start, end=end,
        )
        if frame.empty:
            raise ValueError(f"no clean bars for {symbol} covering the events")
        validate_bars(
            coerce_bars(frame), symbol,
            expected_interval=pd.Timedelta(minutes=config.bar_minutes),
            strict=True,
        )
        bars[symbol] = frame
    return bars


def _momentum_table(close: pd.Series, windows: tuple[int, ...], bar_minutes: int) -> pd.DataFrame:
    return pd.DataFrame(
        {n: log_momentum(close, n, bar_minutes=bar_minutes) for n in windows}
    )


def _eligible_controls(
    index: pd.DatetimeIndex,
    event_opens: pd.Series,
    this_open: pd.Timestamp,
    config: PrecursorConfig,
    *,
    used: set,
) -> np.ndarray:
    local = to_market_time(index)
    tod = (local.hour * 60 + local.minute).to_numpy()
    event_local = to_market_time(pd.DatetimeIndex([this_open]))[0]
    event_tod = int(event_local.hour * 60 + event_local.minute)
    tod_ok = np.abs(tod - event_tod) <= config.tod_tolerance_minutes
    event_times = pd.DatetimeIndex(event_opens)
    excl = np.zeros(len(index), dtype=bool)
    delta = pd.Timedelta(minutes=config.exclusion_minutes)
    for stamp in event_times:
        excl |= np.abs(index - stamp) <= delta
    already = index.isin(pd.DatetimeIndex(list(used))) if used else np.zeros(len(index), dtype=bool)
    return tod_ok & ~excl & ~already & (index != this_open)


def _sample_row(
    *,
    event_id: int,
    sample_type: str,
    is_trend: int,
    symbol: str,
    bar_open: pd.Timestamp,
    trend_start,
    direction: int,
    ztrend: float,
    er: float,
    close: pd.Series,
    moms: pd.DataFrame,
    config: PrecursorConfig,
) -> dict:
    trend_start_ts = pd.Timestamp(trend_start) if pd.notna(trend_start) else bar_open + pd.Timedelta(minutes=1)
    row = {
        "timestamp": trend_start_ts if sample_type == "trend" else bar_open + pd.Timedelta(minutes=1),
        "bar_open": bar_open,
        "trend_start": trend_start if sample_type == "trend" else pd.NaT,
        "symbol": symbol,
        "sample_type": sample_type,
        "is_trend": int(is_trend),
        "direction": int(direction),
        "event_id": event_id,
        "session_date": str(session_date(pd.DatetimeIndex([bar_open])).iloc[0]),
        "ztrend": ztrend,
        "ER": er,
    }
    latest_end = None
    for window in config.mom_windows:
        for lead in config.leads:
            obs = bar_open - pd.Timedelta(minutes=lead * config.bar_minutes)
            factor_end = obs
            value = np.nan
            if obs in moms.index:
                value = float(moms.loc[obs, window])
            name = f"mom_{window}_lead{lead}"
            row[name] = value
            row[f"dmom_{window}_lead{lead}"] = (
                np.nan if not np.isfinite(value) else value * direction
            )
            row[f"factor_end_{name}"] = factor_end if obs in close.index else pd.NaT
            if obs in close.index:
                latest_end = factor_end if latest_end is None else max(latest_end, factor_end)
    row["factor_end_time"] = latest_end if latest_end is not None else pd.NaT
    return row


def _assert_no_leakage(samples: pd.DataFrame) -> None:
    if samples.empty:
        return
    ends = pd.to_datetime(samples["factor_end_time"], utc=True)
    opens = pd.to_datetime(samples["bar_open"], utc=True)
    ok = ends.isna() | (ends < opens)
    if not bool(ok.all()):
        raise AssertionError("factor_end_time must be strictly before bar_open")
    trends = samples.loc[samples["sample_type"] == "trend"]
    if not trends.empty:
        starts = pd.to_datetime(trends["trend_start"], utc=True)
        tend = pd.to_datetime(trends["factor_end_time"], utc=True)
        ok_t = tend.isna() | (tend < starts)
        if not bool(ok_t.all()):
            raise AssertionError("factor_end_time must be strictly before trend_start")


def _one_test(
    samples: pd.DataFrame,
    column: str,
    window: int,
    lead: int,
    side: str,
    direction: int | None,
    *,
    signed: bool,
) -> dict:
    if direction is None:
        block = samples
    else:
        block = samples.loc[samples["direction"] == direction]
    frame = block[["is_trend", column]].dropna()
    y = frame["is_trend"].to_numpy(dtype=float)
    x = frame[column].to_numpy(dtype=float)
    trend = x[y == 1]
    control = x[y == 0]
    baseline = float(y.mean()) if len(y) else np.nan
    buckets = _decile_bucket(pd.Series(x, index=frame.index)) if len(x) else pd.Series(dtype=int)
    top = float(y[buckets.to_numpy() == 9].mean()) if len(y) and (buckets == 9).any() else np.nan
    return {
        "side": side,
        "window": window,
        "lead": lead,
        "column": column,
        "n_trend": int(len(trend)),
        "n_control": int(len(control)),
        "trend_mean": _mean(trend),
        "control_mean": _mean(control),
        "trend_median": _median(trend),
        "control_median": _median(control),
        "mean_diff": _mean(trend) - _mean(control),
        "cohens_d": _cohens_d(trend, control),
        "pearson": _pearson(x, y),
        "spearman": _spearman(x, y),
        "auc": _roc_auc(y, x),
        "baseline_p_trend": baseline,
        "top_decile_p_trend": top,
        "top_decile_lift": top / baseline if baseline and np.isfinite(top) else np.nan,
        "signed": signed,
    }


def _attach_bootstrap(
    stats: pd.DataFrame, samples: pd.DataFrame, config: PrecursorConfig
) -> pd.DataFrame:
    if samples.empty or config.n_bootstrap < 1:
        return stats
    rng = np.random.default_rng(config.random_seed + 1)
    days = samples["session_date"].to_numpy()
    unique = np.unique(days)
    col_index = {
        (row.side, int(row.window), int(row.lead)): i
        for i, row in stats.iterrows()
    }
    diffs = np.full((config.n_bootstrap, len(stats)), np.nan)
    aucs = np.full((config.n_bootstrap, len(stats)), np.nan)
    lifts = np.full((config.n_bootstrap, len(stats)), np.nan)
    for b in range(config.n_bootstrap):
        draw = rng.choice(unique, size=len(unique), replace=True)
        picked = np.concatenate([np.flatnonzero(days == day) for day in draw])
        boot = samples.iloc[picked]
        for _, row in stats.iterrows():
            i = col_index[(row.side, int(row.window), int(row.lead))]
            if row.side == "up":
                block = boot.loc[boot["direction"] == 1]
            elif row.side == "down":
                block = boot.loc[boot["direction"] == -1]
            else:
                block = boot
            col = row["column"]
            frame = block[["is_trend", col]].dropna()
            y = frame["is_trend"].to_numpy(dtype=float)
            x = frame[col].to_numpy(dtype=float)
            if y.size == 0 or y.sum() == 0 or y.sum() == len(y):
                continue
            trend = x[y == 1]
            control = x[y == 0]
            diffs[b, i] = _mean(trend) - _mean(control)
            aucs[b, i] = _roc_auc(y, x)
            baseline = float(y.mean())
            buckets = _decile_bucket(pd.Series(x))
            top = float(y[buckets.to_numpy() == 9].mean()) if (buckets == 9).any() else np.nan
            lifts[b, i] = top / baseline if baseline and np.isfinite(top) else np.nan
    stats = stats.copy()
    stats["diff_ci_lo"] = np.nanpercentile(diffs, 2.5, axis=0)
    stats["diff_ci_hi"] = np.nanpercentile(diffs, 97.5, axis=0)
    stats["auc_ci_lo"] = np.nanpercentile(aucs, 2.5, axis=0)
    stats["auc_ci_hi"] = np.nanpercentile(aucs, 97.5, axis=0)
    stats["lift_ci_lo"] = np.nanpercentile(lifts, 2.5, axis=0)
    stats["lift_ci_hi"] = np.nanpercentile(lifts, 97.5, axis=0)
    return stats


def _write_artifacts(
    output_dir: Path, samples: pd.DataFrame, stats: pd.DataFrame, summary: dict
) -> None:
    public = samples.drop(
        columns=[c for c in samples.columns if c.startswith("factor_end_") and c != "factor_end_time"]
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    public.to_parquet(output_dir / "precursor_samples.parquet", index=False)
    public.to_csv(output_dir / "precursor_samples.csv", index=False)
    stats.to_csv(output_dir / "precursor_statistics.csv", index=False)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


def _summary_text(summary: dict) -> str:
    cells = summary.get("highest_auc_cells") or []
    cell_lines = "\n".join(
        f"  window={c['window']} lead={c['lead']}  AUC={c['auc']:.3f}  "
        f"diff={c['mean_diff']:.5f}  top-decile lift={c['top_decile_lift']:.2f}"
        for c in cells
    ) or "  (none)"
    small = "yes — treat every number as descriptive" if summary["sample_too_small"] else "no"
    return "\n".join(
        [
            "Trend precursor test — descriptive, not a fitted model",
            "",
            f"1. Sample: {summary['n_trend']} trends ({summary['n_up']} UP / "
            f"{summary['n_down']} DOWN), {summary['n_control']} controls, "
            f"{summary['n_days']} sessions. Baseline P(Trend)="
            f"{summary['baseline_p_trend']:.3f}.",
            "",
            "2. Highest-AUC directional cells (display only, not selected parameters):",
            cell_lines,
            "",
            f"3. Directional AUC range {summary['auc_min']:.3f} … {summary['auc_max']:.3f} "
            f"(median {summary['auc_median']:.3f}). "
            "Any bootstrap 95% CI excluding 0.5: "
            f"{summary['any_auc_ci_excludes_0.5']}.",
            "",
            f"4. Median top-decile lift {summary['top_decile_lift_median']:.2f}. "
            "Any mean-diff CI excluding 0: "
            f"{summary['any_mean_diff_ci_excludes_0']}.",
            "",
            f"5. Sample too small for a stable claim: {small}.",
            "",
            "6. Leakage checks: factor_end_time < bar_open and < trend_start; "
            "momentum does not cross a session; controls are ≥ exclusion_window "
            "from every Trend Start. This is not a backtest.",
            "",
            summary["note"],
            "",
        ]
    ) + "\n"


def _mean(x: np.ndarray) -> float:
    return float(np.mean(x)) if len(x) else np.nan


def _median(x: np.ndarray) -> float:
    return float(np.median(x)) if len(x) else np.nan


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return np.nan
    var = ((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1)) / (
        len(a) + len(b) - 2
    )
    if var <= 0:
        return np.nan
    return float((np.mean(a) - np.mean(b)) / np.sqrt(var))


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank(method="average").to_numpy()
    ry = pd.Series(y).rank(method="average").to_numpy()
    return _pearson(rx, ry)


def _roc_auc(y: np.ndarray, scores: np.ndarray) -> float:
    """Mann-Whitney AUC. No sklearn."""
    y = np.asarray(y, dtype=float)
    scores = np.asarray(scores, dtype=float)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))
