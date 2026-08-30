"""Kronos as an entry-confirmation filter.

Kronos is a decoder-only transformer over tokenised OHLCV, pretrained on K-line
data from 45+ exchanges, that forecasts a future OHLCV *path*. It is used here
strictly as a veto on entries a strategy has already proposed — never as a
signal of its own — for three reasons:

* inference is far too slow to evaluate on every bar of every symbol;
* running it only on candidates keeps the comparison clean: the same candidate
  set is backtested with and without the filter, so the difference is
  attributable;
* a forecast path is not a position. Turning one into a trade needs a decision
  rule, and confining that rule to "agree or veto" keeps its free parameters to
  one threshold.

Causality
---------
The context window ends **at the candidate bar inclusive** and the forecast
begins after it. The strategy then acts one bar later, as every other signal
does. Nothing from the forecast horizon is visible to the model.

What the score means
--------------------
The implied return of the forecast path over `pred_len` bars, expressed in bars
of the symbol's own volatility, so it is comparable across names:

    score = log(forecast_close[-1] / last_actual_close) / (sigma * sqrt(pred_len))

Positive means the model expects a rise. A long is confirmed when the score is
at or above `confirmation_min`, a short when it is at or below its negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

#: Bars of context fed to the model. 128 x 5 minutes is about 1.6 sessions and
#: sits inside the 512-token context of Kronos-small at good throughput.
DEFAULT_LOOKBACK = 128

#: Windows per forward pass. Measured on an M3 Pro: 44 ms/window at this size.
DEFAULT_BATCH = 128


@dataclass
class Candidate:
    """One entry the strategy proposed, and where its context window ends."""

    position: int          # index into the panel
    symbol: str
    direction: int


def collect_candidates(indicators: dict[str, pd.DataFrame], index: pd.DatetimeIndex,
                       lookback: int = DEFAULT_LOOKBACK) -> list[Candidate]:
    """Every bar at which the strategy proposed an entry, with enough history."""
    positions = {t: i for i, t in enumerate(index)}
    found: list[Candidate] = []
    for symbol, frame in indicators.items():
        if "candidate" not in frame:
            continue
        fired = frame["candidate"]
        for timestamp, direction in fired[fired != 0].items():
            i = positions[timestamp]
            if i >= lookback:
                found.append(Candidate(i, symbol, int(np.sign(direction))))
    return sorted(found, key=lambda c: (c.position, c.symbol))


def score_candidates(
    candidates: list[Candidate],
    panel,
    volatility: pd.DataFrame,
    predictor,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    pred_len: int = 12,
    batch_size: int = DEFAULT_BATCH,
    temperature: float = 1.0,
    top_p: float = 0.9,
    sample_count: int = 1,
    progress=None,
) -> pd.DataFrame:
    """Run the model over every candidate and return a timestamp x symbol score frame.

    ``predictor`` is a ``KronosPredictor``; it is injected rather than
    constructed here so this module has no hard dependency on torch and stays
    importable (and testable) without it.
    """
    index = panel.index
    scores = pd.DataFrame(np.nan, index=index, columns=list(panel.symbols))
    if not candidates:
        return scores

    fields = {name: panel.field(name) for name in ("open", "high", "low", "close")}
    volume = panel.volume

    for chunk, frames, forecasts in _run_batches(
        candidates, fields, volume, index, predictor,
        lookback=lookback, pred_len=pred_len, batch_size=batch_size,
        temperature=temperature, top_p=top_p, sample_count=sample_count,
        progress=progress,
    ):
        for candidate, frame, forecast in zip(chunk, frames, forecasts):
            last = float(frame["close"].iloc[-1])
            target = float(forecast["close"].iloc[-1])
            sigma = volatility[candidate.symbol].iat[candidate.position]
            if not (np.isfinite(last) and np.isfinite(target) and np.isfinite(sigma)) \
                    or last <= 0 or target <= 0 or sigma <= 0:
                continue
            scores.iat[candidate.position, scores.columns.get_loc(candidate.symbol)] = (
                np.log(target / last) / (sigma * np.sqrt(pred_len))
            )

    return scores


def forecast_paths(
    candidates: list[Candidate],
    panel,
    predictor,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    pred_len: int = 12,
    batch_size: int = DEFAULT_BATCH,
    temperature: float = 1.0,
    top_p: float = 0.9,
    sample_count: int = 1,
    progress=None,
) -> dict[tuple[pd.Timestamp, str], pd.DataFrame]:
    """The forecast OHLCV paths themselves, keyed by ``(timestamp, symbol)``.

    `score_candidates` reduces each path to one number, which is all a filter
    needs. A chart needs the path. Same windows, same causality — this is only
    worth running over a handful of candidates, because the paths are large.
    """
    index = panel.index
    fields = {name: panel.field(name) for name in ("open", "high", "low", "close")}
    paths: dict[tuple[pd.Timestamp, str], pd.DataFrame] = {}

    for chunk, _, forecasts in _run_batches(
        candidates, fields, panel.volume, index, predictor,
        lookback=lookback, pred_len=pred_len, batch_size=batch_size,
        temperature=temperature, top_p=top_p, sample_count=sample_count,
        progress=progress,
    ):
        step = index[1] - index[0]
        for candidate, forecast in zip(chunk, forecasts):
            forecast = forecast.copy()
            forecast.index = [
                index[candidate.position] + step * (k + 1) for k in range(len(forecast))
            ]
            paths[(index[candidate.position], candidate.symbol)] = forecast
    return paths


def _run_batches(candidates, fields, volume, index, predictor, *, lookback, pred_len,
                 batch_size, temperature, top_p, sample_count, progress):
    """Yield ``(candidates, context frames, forecasts)`` one batch at a time.

    The window ends at the candidate bar inclusive. That single slice is the
    causality guarantee for everything built on this module, so it lives in one
    place rather than being repeated per caller.
    """
    step = index[1] - index[0]
    price_columns = ["open", "high", "low", "close"]

    for start in range(0, len(candidates), batch_size):
        frames, history, future, kept = [], [], [], []
        for candidate in candidates[start : start + batch_size]:
            window = slice(candidate.position - lookback + 1, candidate.position + 1)
            frame = pd.DataFrame(
                {name: fields[name][candidate.symbol].to_numpy()[window]
                 for name in price_columns}
            )
            frame["volume"] = np.nan_to_num(
                volume[candidate.symbol].to_numpy()[window], nan=0.0
            )
            # A symbol with no print yet in a session has no price. Forward
            # filling uses only earlier bars, so it is causal; a window whose
            # *leading* bars are still empty is skipped rather than invented.
            frame[price_columns] = frame[price_columns].ffill()
            if frame[price_columns].isna().to_numpy().any():
                continue

            frames.append(frame)
            history.append(pd.Series(index[window]))
            future.append(pd.Series([index[candidate.position] + step * (k + 1)
                                     for k in range(pred_len)]))
            kept.append(candidate)

        if frames:
            yield kept, frames, predictor.predict_batch(
                df_list=frames, x_timestamp_list=history, y_timestamp_list=future,
                pred_len=pred_len, T=temperature, top_p=top_p,
                sample_count=sample_count, verbose=False,
            )
        if progress is not None:
            progress(min(start + batch_size, len(candidates)), len(candidates))


def save_scores(scores: pd.DataFrame, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    scores.to_parquet(path)
    return path


def load_scores(path: Path | str) -> pd.DataFrame:
    return pd.read_parquet(path)
