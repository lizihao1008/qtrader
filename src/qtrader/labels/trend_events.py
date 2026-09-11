"""Minute-level trend *labels* — they look into the future on purpose.

Given a 1-minute bar ``t``, ask whether the next ``H`` minutes are a clean
directional move: the path does not reverse through ``close_t`` before it has
led in the labelled direction, the net move is large enough in random-walk
units, the path is efficient, and later adverse excursion stays bounded.
Consecutive candidates that describe the same move collapse to one Trend Start.

These numbers are for offline inspection and later supervised research. They
are not an entry rule. Thresholds in :class:`TrendDetectConfig` are V1
heuristics, not fitted parameters.

The store timestamps bars at the **open**. Event tables report ``trend_start``
as the bar's **end** (open + 1 minute), which is when ``close_t`` is known.
``bar_open`` is kept so a chart can join back to the candle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import numpy as np
import pandas as pd

from ..data.schema import coerce_bars, validate_bars
from ..data.sessions import session_date
from ..features.relative import bar_log_returns

UP, DOWN, FLAT = 1, -1, 0


@dataclass(frozen=True)
class TrendDetectConfig:
    """V1 heuristic for a clean H-minute trend. Not a search result."""

    horizon: int = 30
    vol_lookback: int = 120
    sigma_floor: float = 1e-6
    z_threshold: float = 1.5
    er_threshold: float = 0.40
    mae_threshold: float = 0.50
    require_first_bar_aligned: bool = True
    cooldown_bars: int | None = None
    bar_minutes: int = 1

    def __post_init__(self) -> None:
        if self.horizon < 2:
            raise ValueError("horizon must be at least 2 bars")
        if self.vol_lookback < 2:
            raise ValueError("vol_lookback must be at least 2 bars")
        if self.sigma_floor <= 0:
            raise ValueError("sigma_floor must be positive")
        if self.z_threshold <= 0:
            raise ValueError("z_threshold must be positive")
        if not 0.0 <= self.er_threshold <= 1.0:
            raise ValueError("er_threshold must lie in [0, 1]")
        if self.mae_threshold < 0:
            raise ValueError("mae_threshold cannot be negative")
        if self.cooldown_bars is not None and self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")
        if self.bar_minutes < 1:
            raise ValueError("bar_minutes must be at least 1")

    @property
    def cooldown(self) -> int:
        """Bars of same-direction silence after a run's *last* candidate."""
        return self.horizon if self.cooldown_bars is None else self.cooldown_bars

    @property
    def bar_delta(self) -> pd.Timedelta:
        return pd.Timedelta(minutes=self.bar_minutes)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "TrendDetectConfig":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in allowed})


def detect_trend_events(
    bars: pd.DataFrame, symbol: str, config: TrendDetectConfig | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(candidates, events)``. Candidates are every ±1 bar; events are starts."""
    config = config or TrendDetectConfig()
    scored = score_trend_candidates(bars, symbol, config)
    candidates = scored.loc[scored["candidate_direction"] != FLAT].copy()
    events = deduplicate_trend_events(scored, config)
    return candidates, events


def score_trend_candidates(
    bars: pd.DataFrame, symbol: str, config: TrendDetectConfig
) -> pd.DataFrame:
    """One row per bar: future-H statistics and a ±1/0 candidate flag.

    ``sigma`` at ``t`` uses only returns ``<= t``. Everything else at ``t``
    uses the open interval ``(t, t+H]`` and is a label, not a feature.
    When ``require_first_bar_aligned``, the path may not go against
    ``close_t`` by more than it has already gone with the label — a one-tick
    first close does not license an immediate reversal.
    """
    bars = coerce_bars(bars)
    validate_bars(bars, symbol, expected_interval=config.bar_delta, strict=True)
    if not bars.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: timestamps are not ascending")

    close = bars["close"].astype(float)
    session = session_date(close.index)
    returns = bar_log_returns(close.to_frame("r"), within_session=True)["r"]
    H = config.horizon
    scale = np.sqrt(H)

    past_ok = _exact_span(close.index, session, -(config.vol_lookback - 1), config.bar_delta)
    future_ok = _exact_span(close.index, session, H, config.bar_delta)

    sigma_raw = returns.groupby(session.to_numpy()).transform(
        lambda s: s.rolling(config.vol_lookback, min_periods=config.vol_lookback).std()
    )
    sigma = sigma_raw.where(past_ok).clip(lower=config.sigma_floor)

    path = _future_path(close.to_numpy(dtype=float), H)
    path[~future_ok.to_numpy()] = np.nan
    complete = np.isfinite(path).all(axis=1)

    r_future = path[:, -1]
    first = path[:, 0]
    travel = np.nansum(np.abs(np.diff(np.concatenate(
        [np.zeros((len(close), 1)), path], axis=1
    ), axis=1)), axis=1)
    # ER on r_{t+1:t+H}: |sum r| / sum |r|. sum r = C_H; |r_k| = |C_k - C_{k-1}|.
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.abs(r_future) / np.where(travel > 0, travel, np.nan)
    er = np.where(complete, er, np.nan)
    r_future = np.where(complete, r_future, np.nan)

    min_c = np.where(complete, np.min(path, axis=1), np.nan)
    max_c = np.where(complete, np.max(path, axis=1), np.nan)
    mae_up = np.maximum(0.0, -min_c)
    mae_down = np.maximum(0.0, max_c)
    mfe_up = max_c
    mfe_down = np.maximum(0.0, -min_c)

    denom = (sigma.to_numpy(dtype=float) * scale)
    with np.errstate(divide="ignore", invalid="ignore"):
        ztrend = r_future / denom
        mae_up_n = mae_up / denom
        mae_down_n = mae_down / denom
        mfe_up_n = mfe_up / denom
        mfe_down_n = mfe_down / denom

    scored = np.isfinite(ztrend) & np.isfinite(er)
    if config.require_first_bar_aligned:
        first_up = _lead_before_reversal(path, UP)
        first_down = _lead_before_reversal(path, DOWN)
    else:
        first_up = np.ones(len(close), dtype=bool)
        first_down = np.ones(len(close), dtype=bool)
    up = scored & first_up & (ztrend >= config.z_threshold) & (er >= config.er_threshold) & (
        mae_up_n <= config.mae_threshold
    )
    down = scored & first_down & (ztrend <= -config.z_threshold) & (er >= config.er_threshold) & (
        mae_down_n <= config.mae_threshold
    )
    direction = np.where(up, UP, np.where(down, DOWN, FLAT)).astype(int)

    end_close = pd.Series(close.to_numpy(), index=close.index).shift(-H)
    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "bar_open": close.index,
            "trend_start": close.index + config.bar_delta,
            "candidate_direction": direction,
            "horizon": H,
            "future_return": r_future,
            "first_return": first,
            "ztrend": ztrend,
            "ER": er,
            "sigma": sigma.to_numpy(dtype=float),
            "MAE_up": mae_up,
            "MAE_down": mae_down,
            "MAE_up_norm": mae_up_n,
            "MAE_down_norm": mae_down_n,
            "MFE_up": mfe_up,
            "MFE_down": mfe_down,
            "MFE_up_norm": mfe_up_n,
            "MFE_down_norm": mfe_down_n,
            "start_close": close.to_numpy(dtype=float),
            "end_close": end_close.to_numpy(dtype=float),
            "valid_sigma": past_ok.to_numpy(),
            "valid_future": complete,
        },
        index=close.index,
    )
    frame.index.name = "timestamp"
    return frame


def deduplicate_trend_events(
    scored: pd.DataFrame, config: TrendDetectConfig
) -> pd.DataFrame:
    """Keep the first bar of each same-direction candidate run.

    A run is consecutive ±1 bars, one minute apart, same session. After an
    event, same-direction runs may not start until ``cooldown`` bars after
    *this run ended* — measuring from the first bar would re-slice one visual
    grind into a new event every H minutes. An opposite-direction start in
    that window is still emitted and flagged ``overlaps_opposite``.
    """
    if scored.empty:
        return _empty_events()

    rows = []
    for symbol, block in scored.groupby("symbol", sort=False):
        rows.append(_dedup_one_symbol(block, config))
    events = pd.concat(rows, ignore_index=True) if rows else _empty_events()
    return events.sort_values(["symbol", "bar_open"]).reset_index(drop=True)


def _dedup_one_symbol(block: pd.DataFrame, config: TrendDetectConfig) -> pd.DataFrame:
    direction = block["candidate_direction"].to_numpy(dtype=int)
    index = pd.DatetimeIndex(block.index)
    session = session_date(index).to_numpy()
    bar_delta = config.bar_delta
    cooldown = config.cooldown * bar_delta
    never = pd.Timestamp.min.tz_localize("UTC")

    n = len(block)
    events = []
    i = 0
    cool_until = {UP: never, DOWN: never}
    while i < n:
        d = int(direction[i])
        if d == FLAT:
            i += 1
            continue
        j = i + 1
        while j < n and int(direction[j]) == d:
            adjacent = (index[j] - index[j - 1] == bar_delta) and (session[j] == session[j - 1])
            if not adjacent:
                break
            j += 1
        run_length = j - i
        start_at = index[i]
        other = DOWN if d == UP else UP
        overlaps = start_at < cool_until[other]
        if start_at >= cool_until[d]:
            row = block.iloc[i]
            mae = float(row["MAE_up"] if d == UP else row["MAE_down"])
            mae_n = float(row["MAE_up_norm"] if d == UP else row["MAE_down_norm"])
            mfe = float(row["MFE_up"] if d == UP else row["MFE_down"])
            mfe_n = float(row["MFE_up_norm"] if d == UP else row["MFE_down_norm"])
            events.append(
                {
                    "symbol": row["symbol"],
                    "trend_start": row["trend_start"],
                    "bar_open": row["bar_open"],
                    "direction": d,
                    "horizon": int(row["horizon"]),
                    "future_return": float(row["future_return"]),
                    "first_return": float(row["first_return"]),
                    "ztrend": float(row["ztrend"]),
                    "ER": float(row["ER"]),
                    "sigma": float(row["sigma"]),
                    "MAE": mae,
                    "MAE_norm": mae_n,
                    "MFE": mfe,
                    "MFE_norm": mfe_n,
                    "start_close": float(row["start_close"]),
                    "end_close": float(row["end_close"]),
                    "candidate_run_length": int(run_length),
                    "overlaps_opposite": bool(overlaps),
                }
            )
            cool_until[d] = index[j - 1] + cooldown
        i = j
    return pd.DataFrame(events) if events else _empty_events()


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol", "trend_start", "bar_open", "direction", "horizon",
            "future_return", "first_return", "ztrend", "ER", "sigma", "MAE", "MAE_norm",
            "MFE", "MFE_norm", "start_close", "end_close",
            "candidate_run_length", "overlaps_opposite",
        ]
    )


def _exact_span(
    index: pd.DatetimeIndex,
    session: pd.Series,
    offset: int,
    bar_delta: pd.Timedelta,
) -> pd.Series:
    """True at ``t`` iff the bar ``t+offset`` exists, same session, exact time span.

    A missing minute inside the window fails the time-span check, so a rolling
    statistic is never allowed to jump a gap or the overnight break.
    """
    n = len(index)
    sess = session.to_numpy()
    expected = offset * bar_delta
    target = np.arange(n) + offset
    valid = (target >= 0) & (target < n)
    out = np.zeros(n, dtype=bool)
    idx = np.flatnonzero(valid)
    dest = target[idx]
    span = index[dest] - index[idx]
    out[idx] = (sess[dest] == sess[idx]) & (span == expected)
    return pd.Series(out, index=index)


def _lead_before_reversal(path: np.ndarray, side: int) -> np.ndarray:
    """True when the path does not go against ``close_t`` by more than it has led.

    ``path[t, k] = log(close[t+k+1] / close[t])``. At the first prefix that
    goes underwater relative to the start, the running favourable extreme
    must already exceed that dip. A one-tick first close then a larger
    reversal fails; a later pullback smaller than the lead so far passes.
    """
    signed = path * side
    with np.errstate(invalid="ignore"):
        peak = np.maximum.accumulate(signed, axis=1)
        trough = np.minimum.accumulate(signed, axis=1)
        reversed_first = (trough < 0) & (peak <= -trough)
    return ~reversed_first.any(axis=1)


def _future_path(close: np.ndarray, horizon: int) -> np.ndarray:
    """``path[t, k] = log(close[t+k+1] / close[t])`` for ``k = 0 .. H-1``."""
    n = len(close)
    path = np.full((n, horizon), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        for k in range(1, horizon + 1):
            path[: n - k, k - 1] = np.log(close[k:] / close[: n - k])
    return path
