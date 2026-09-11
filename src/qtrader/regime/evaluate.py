"""Offline scores for a causal regime detector.

Labels may look into the future. They never enter Kalman, CUSUM, ER, or the
state machine. The live path is :meth:`KalmanCUSUMRegimeDetector.update`; this
module only asks how early that path fired, and how often it fired in chop.

The objective is detection delay versus false-alarm rate, not classification
accuracy on a balanced UP/DOWN/FLAT label.

Two complementary false-alarm measures live here. :func:`evaluate_regime`
counts entries on real bars that no labelled event covers — it depends on the
label thresholds, and on whether the label pipeline could score those bars at
all. :func:`null_entry_rate` needs neither: it replays the detector over
**simulated driftless random walks**, where every entry is false by
construction. That is the number a threshold set should be quoted with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from .config import KalmanCUSUMConfig
from .detector import DOWN, FLAT, UP, KalmanCUSUMRegimeDetector

STATE_TO_DIR = {UP: 1, DOWN: -1, FLAT: 0}


def evaluate_regime(
    result: pd.DataFrame,
    events: pd.DataFrame | None = None,
    *,
    max_delay: int = 30,
    horizon: int | None = None,
    bars_per_session: int = 390,
) -> dict[str, float]:
    """Delay / FAR / flip / capture / duration on one replay.

    ``events`` is the table from :func:`qtrader.labels.trend_events.detect_trend_events`
    (or anything with ``bar_open`` and ``direction`` in {+1, −1}). If it is
    omitted, only path statistics (flips, duration) are returned.
    """
    n = len(result)
    if n == 0:
        return _empty_metrics()

    states = result["state"].astype(str)
    if "state_changed" in result.columns:
        changed = result["state_changed"].to_numpy(dtype=bool)
    else:
        changed = states.ne(states.shift(1, fill_value=FLAT)).to_numpy()

    runs = _run_lengths(states.to_numpy())
    up_dur = [length for state, length in runs if state == UP]
    down_dur = [length for state, length in runs if state == DOWN]
    trend_dur = [length for state, length in runs if state != FLAT]
    n_sessions = _n_sessions(result.index)
    n_flips = int(changed.sum())
    entries = _entry_times(result)

    metrics: dict[str, float] = {
        "n_bars": float(n),
        "n_sessions": float(n_sessions),
        "n_flips": float(n_flips),
        "flips_per_session": n_flips / max(n_sessions, 1),
        "flips_per_hour": n_flips / max(n / 60.0, 1e-9),
        "n_up_entries": float(sum(1 for _, d in entries if d == 1)),
        "n_down_entries": float(sum(1 for _, d in entries if d == -1)),
        "mean_up_duration": float(np.mean(up_dur)) if up_dur else float("nan"),
        "mean_down_duration": float(np.mean(down_dur)) if down_dur else float("nan"),
        "mean_trend_duration": float(np.mean(trend_dur)) if trend_dur else float("nan"),
        "frac_up": float((states == UP).mean()),
        "frac_down": float((states == DOWN).mean()),
        "frac_flat": float((states == FLAT).mean()),
    }

    if events is not None and events.empty:
        raise ValueError(
            "events is an empty table, so delay and false-alarm metrics would all be NaN. "
            "detect_trend_events needs gap-free minutes inside vol_lookback and horizon; "
            "on a feed with missing bars (IEX QQQ drops ~4 minutes a session) a short "
            "window can score zero bars. Widen the window, pick a symbol that prints "
            "every minute, or pass events=None for path statistics only."
        )
    if events is None:
        metrics.update(
            {
                "n_events": 0.0,
                "n_captured": 0.0,
                "capture_ratio": float("nan"),
                "mean_delay": float("nan"),
                "median_delay": float("nan"),
                "n_false_alarms": float("nan"),
                "false_alarms_per_session": float("nan"),
                "false_alarm_rate": float("nan"),
            }
        )
        return metrics

    horizon_bars = int(horizon if horizon is not None else events["horizon"].iloc[0])
    _, delays, false_alarms = _match_events(
        result, events, max_delay=max_delay, horizon=horizon_bars
    )
    n_events = int(len(delays))
    n_hit = int(np.isfinite(delays).sum())
    metrics.update(
        {
            "n_events": float(n_events),
            "n_captured": float(n_hit),
            "capture_ratio": n_hit / n_events if n_events else float("nan"),
            "mean_delay": float(np.nanmean(delays)) if n_hit else float("nan"),
            "median_delay": float(np.nanmedian(delays)) if n_hit else float("nan"),
            "n_false_alarms": float(false_alarms),
            "false_alarms_per_session": false_alarms / max(n_sessions, 1),
            "false_alarm_rate": false_alarms / max(n / max(bars_per_session, 1), 1e-9),
        }
    )
    return metrics


def delay_far_grid(
    bars: pd.DataFrame,
    events: pd.DataFrame,
    grid: Sequence[Mapping[str, Any]],
    *,
    base: KalmanCUSUMConfig | None = None,
    max_delay: int = 30,
    horizon: int | None = None,
) -> pd.DataFrame:
    """Replay the detector under each parameter dict; return delay/FAR rows.

    Each grid entry is merged onto ``base`` (balanced defaults if omitted).
    The detector is constructed fresh for every cell, so cells cannot leak
    state into each other. Future information stays inside ``events``.
    """
    base = base or KalmanCUSUMConfig()
    rows = []
    for cell in grid:
        cfg = base.replace(**dict(cell))
        detector = KalmanCUSUMRegimeDetector(cfg)
        result = detector.run(bars)
        metrics = evaluate_regime(result, events, max_delay=max_delay, horizon=horizon)
        row = {**dict(cell), **metrics}
        rows.append(row)
    return pd.DataFrame(rows)


def null_entry_rate(
    config: KalmanCUSUMConfig | None = None,
    *,
    n_sessions: int = 200,
    bars_per_session: int = 390,
    sigma: float = 8e-4,
    seed: int = 0,
) -> dict[str, float]:
    """Replay the detector over simulated driftless random walks.

    There is no trend in the input, so **every** entry is a false alarm. This
    is what makes a threshold set quotable: "``entry_z=2`` costs N false
    entries per session" is a statement about the detector, not about a
    particular symbol, feed, or label configuration.

    Returns entries per session, the share of bars spent in a trend state, and
    the flip rate — the same vocabulary :func:`evaluate_regime` reports, so the
    two can be read side by side.
    """
    config = config or KalmanCUSUMConfig()
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2020-01-06", periods=n_sessions, tz="UTC")
    index = pd.DatetimeIndex(
        np.concatenate(
            [
                (day + pd.Timedelta(hours=13, minutes=30)).value
                + np.arange(bars_per_session) * 60_000_000_000
                for day in days
            ]
        )
    ).tz_localize("UTC")

    steps = rng.standard_normal((n_sessions, bars_per_session)) * sigma
    steps[:, 0] = 0.0
    log_price = np.log(100.0) + np.cumsum(steps, axis=1)
    bars = pd.DataFrame({"close": np.exp(log_price).ravel()}, index=index)
    bars.index.name = "timestamp"

    result = KalmanCUSUMRegimeDetector(config).run(bars)
    metrics = evaluate_regime(result, None, bars_per_session=bars_per_session)
    entries = metrics["n_up_entries"] + metrics["n_down_entries"]
    return {
        "null_entries_per_session": entries / max(n_sessions, 1),
        "null_frac_in_trend": 1.0 - metrics["frac_flat"],
        "null_flips_per_session": metrics["flips_per_session"],
        "null_mean_trend_duration": metrics["mean_trend_duration"],
        "n_sessions": float(n_sessions),
    }


def describe_entries(
    bars: pd.DataFrame,
    result: pd.DataFrame,
    *,
    offsets: Sequence[int] = (-30, -20, -10, -5, 5, 10, 20, 30),
) -> pd.DataFrame:
    """Signed move around each entry bar, in random-walk units. Evaluation only.

    Splits the one question people actually ask of a regime detector into the
    two it really is:

    * **negative offsets** — the move the detector was reacting to. This is what
      "the state is UP" *claims*, and a correct detector makes it large and
      positive. Under a driftless random walk it would be 0.
    * **positive offsets** — what happened after the state was declared. This is
      a *forecast* claim, which a state detector does not make. Reading a large
      number here would be the finding; reading zero is the expected result and
      the reason the state belongs in a gate, not in a score.

    Both are ``sign * (log P[i+k] - log P[i]) / (sigma * sqrt(|k|))`` where
    ``sign`` is +1 for an UP entry and −1 for a DOWN entry, so the columns are
    comparable to each other and across symbols. Offsets that would cross a
    session boundary are dropped rather than reaching into another day.
    """
    from ..data.sessions import session_date

    if "close" not in bars.columns:
        raise KeyError("bar frame must contain a 'close' column")
    log_price = np.log(bars["close"].to_numpy(dtype=float))
    session = session_date(pd.DatetimeIndex(bars.index)).to_numpy()
    steps = np.diff(log_price, prepend=log_price[0])
    steps[np.r_[True, session[1:] != session[:-1]]] = np.nan
    sigma = float(np.nanstd(steps))
    if not np.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("cannot scale entries: the bar returns have no dispersion")

    states = result["state"].astype(str).to_numpy()
    changed = (
        result["state_changed"].to_numpy(dtype=bool)
        if "state_changed" in result.columns
        else np.r_[True, states[1:] != states[:-1]]
    )
    buckets: dict[int, list[float]] = {int(k): [] for k in offsets}
    n_entries = 0
    for i in np.flatnonzero(changed):
        direction = STATE_TO_DIR.get(states[i], 0)
        if direction == 0:
            continue
        n_entries += 1
        for k in buckets:
            j = i + k
            if j < 0 or j >= len(log_price) or session[j] != session[i]:
                continue
            # A backward window is measured from the past towards the entry.
            move = (log_price[j] - log_price[i]) * (1.0 if k > 0 else -1.0)
            buckets[k].append(direction * move / (sigma * np.sqrt(abs(k))))

    rows = [
        {
            "offset": k,
            "kind": "before (what it is describing)" if k < 0 else "after (a forecast claim)",
            "n": len(values),
            "mean_move_rw": float(np.mean(values)) if values else float("nan"),
            "frac_right_way": float(np.mean(np.asarray(values) > 0)) if values else float("nan"),
        }
        for k, values in sorted(buckets.items())
    ]
    out = pd.DataFrame(rows)
    out.attrs["n_entries"] = n_entries
    return out


def default_kh_grid() -> list[dict[str, float]]:
    """The first-round (k, h) grid from the detector spec."""
    return [
        {"cusum_k": k, "cusum_h": h}
        for k in (0.15, 0.25, 0.4)
        for h in (2.0, 3.0, 4.0, 5.0)
    ]


def _empty_metrics() -> dict[str, float]:
    keys = (
        "n_bars",
        "n_sessions",
        "n_flips",
        "flips_per_session",
        "flips_per_hour",
        "n_up_entries",
        "n_down_entries",
        "mean_up_duration",
        "mean_down_duration",
        "mean_trend_duration",
        "frac_up",
        "frac_down",
        "frac_flat",
        "n_events",
        "n_captured",
        "capture_ratio",
        "mean_delay",
        "median_delay",
        "n_false_alarms",
        "false_alarms_per_session",
        "false_alarm_rate",
    )
    return {key: float("nan") for key in keys}


def _n_sessions(index: pd.Index) -> int:
    if not isinstance(index, pd.DatetimeIndex) or len(index) == 0:
        return 1
    from ..data.sessions import session_date

    return int(session_date(index).nunique())


def _run_lengths(states: np.ndarray) -> list[tuple[str, int]]:
    if len(states) == 0:
        return []
    runs: list[tuple[str, int]] = []
    current = str(states[0])
    length = 1
    for value in states[1:]:
        if value == current:
            length += 1
        else:
            runs.append((current, length))
            current = str(value)
            length = 1
    runs.append((current, length))
    return runs


def _entry_times(result: pd.DataFrame) -> list[tuple[pd.Timestamp, int]]:
    """Bars where the detector newly enters UP or DOWN (including reversals)."""
    states = result["state"].astype(str)
    prev = states.shift(1, fill_value=FLAT)
    out: list[tuple[pd.Timestamp, int]] = []
    for ts, state, was in zip(result.index, states, prev):
        if state == was:
            continue
        direction = STATE_TO_DIR.get(state, 0)
        if direction == 0:
            continue
        out.append((ts, direction))
    return out


def _match_events(
    result: pd.DataFrame,
    events: pd.DataFrame,
    *,
    max_delay: int,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    index = result.index
    states = result["state"].astype(str).to_numpy()
    n = len(result)
    covered_up = np.zeros(n, dtype=bool)
    covered_down = np.zeros(n, dtype=bool)
    delays = []

    for _, event in events.iterrows():
        stamp = pd.Timestamp(event["bar_open"])
        loc = index.get_indexer([stamp], method=None)[0]
        if loc < 0:
            delays.append(np.nan)
            continue
        direction = int(event["direction"])
        want = UP if direction > 0 else DOWN
        end = min(n, loc + max_delay + 1)
        hit = np.where(states[loc:end] == want)[0]
        if len(hit):
            delays.append(float(hit[0]))
        else:
            delays.append(np.nan)
        cover_end = min(n, loc + horizon)
        if direction > 0:
            covered_up[loc:cover_end] = True
        else:
            covered_down[loc:cover_end] = True

    captured = np.isfinite(delays)
    false_alarms = 0
    for ts, direction in _entry_times(result):
        loc = index.get_indexer([ts])[0]
        if loc < 0:
            continue
        covered = covered_up if direction > 0 else covered_down
        if not covered[loc]:
            false_alarms += 1
    return captured, np.asarray(delays, dtype=float), false_alarms
