"""Does the score predict, and does it predict *monotonically*?

A backtest answers "did this rule make money", which conflates the signal with
the exits, the book, the costs and the clock. This answers the prior question
directly, on the panel rather than on the trades: **bucket every (bar, symbol)
score and look at what happened next.**

A usable score shows a gradient across the buckets. A single extreme bucket with
flat neighbours is a handful of observations, not an effect — and a U-shape
means the score is measuring magnitude, not direction.

Two windows are reported, and the difference between them is the point.
``fwd_*`` runs from the decision bar's close, which is what the score saw.
``tradeable_*`` runs from the **next bar's open**, which is the earliest price a
fill can reach (ADR-0002). Anything that accrues between the two is real and
unreachable: R31 measured 58% of this score's decile spread landing there.
Quoting only the close-to-close number overstates a signal by whatever the
market moves before the order can be sent.

Neither window crosses a session boundary: a 30-minute forward return taken at
15:45 would otherwise reach into tomorrow's open, which is the overnight gap
wearing a costume.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date

#: Horizons reported by default, in bars. On a 1-minute grid these are minutes.
DEFAULT_HORIZONS = (5, 15, 30)


def forward_returns(
    close: pd.DataFrame,
    horizons=DEFAULT_HORIZONS,
    *,
    entry: pd.DataFrame | None = None,
    entry_lag: int = 0,
) -> dict[int, pd.DataFrame]:
    """Log return to ``h`` bars after the decision, within the session.

    ``entry`` (with ``entry_lag``) replaces the decision bar's close as the
    starting price: pass the open frame with ``entry_lag=1`` to measure from the
    bar a fill would actually land on.
    """
    day = pd.Series(session_date(close.index).to_numpy(), index=close.index)
    start = np.log(close) if entry is None else np.log(entry).shift(-entry_lag)
    out = {}
    for h in horizons:
        ahead = np.log(close).shift(-h)
        same = (day.shift(-h).to_numpy() == day.to_numpy()) & (
            day.shift(-entry_lag).to_numpy() == day.to_numpy()
        )
        out[int(h)] = (ahead - start).where(
            pd.DataFrame(
                np.repeat(same[:, None], close.shape[1], axis=1),
                index=close.index, columns=close.columns,
            )
        )
    return out


def score_buckets(
    score: pd.DataFrame,
    close: pd.DataFrame,
    *,
    eligible: pd.DataFrame | None = None,
    horizons=DEFAULT_HORIZONS,
    n_buckets: int = 10,
    open_: pd.DataFrame | None = None,
    entry_lag: int = 1,
) -> pd.DataFrame:
    """Mean forward return per score bucket, in basis points.

    Buckets are quantiles of the pooled score, so each carries the same number
    of observations and a thin tail cannot masquerade as a bucket.
    """
    forwards = forward_returns(close, horizons)
    tradeable = (
        {}
        if open_ is None
        else forward_returns(close, horizons, entry=open_, entry_lag=entry_lag)
    )
    frame = pd.DataFrame({"score": score.stack(future_stack=True)})
    if eligible is not None:
        mask = eligible.reindex(index=score.index, columns=score.columns)
        frame = frame.loc[mask.fillna(False).stack(future_stack=True).to_numpy()]
    for h, values in forwards.items():
        frame[f"fwd_{h}"] = values.stack(future_stack=True)
    for h, values in tradeable.items():
        frame[f"tradeable_{h}"] = values.stack(future_stack=True)
    frame = frame.dropna(subset=["score"])
    if frame["score"].nunique() < n_buckets:
        return pd.DataFrame()

    frame["bucket"] = pd.qcut(
        frame["score"], n_buckets, labels=False, duplicates="drop"
    )
    grouped = frame.groupby("bucket")
    table = pd.DataFrame({
        "score_low": grouped["score"].min(),
        "score_high": grouped["score"].max(),
        "n": grouped.size(),
    })
    for h in forwards:
        table[f"fwd_{h}_bps"] = grouped[f"fwd_{h}"].mean() * 1e4
        table[f"fwd_{h}_hit"] = grouped[f"fwd_{h}"].apply(
            lambda s: float((s > 0).mean())
        )
    for h in tradeable:
        table[f"tradeable_{h}_bps"] = grouped[f"tradeable_{h}"].mean() * 1e4
    return table


def monotonicity(table: pd.DataFrame, horizon: int, *, prefix: str = "fwd") -> dict:
    """How ordered the buckets are, and how big the end-to-end spread is.

    ``spearman`` over the bucket means is the honest headline: +1 is a perfectly
    ordered score, 0 is none. ``top_minus_bottom`` says whether the ordering is
    worth anything in basis points, which a rank statistic alone will not tell
    you.
    """
    column = f"{prefix}_{horizon}_bps"
    if table.empty or column not in table:
        return {}
    means = table[column].to_numpy(dtype=float)
    buckets = np.arange(len(means), dtype=float)
    ranks_a = pd.Series(buckets).rank().to_numpy()
    ranks_b = pd.Series(means).rank().to_numpy()
    return {
        "horizon": horizon,
        "spearman": float(np.corrcoef(ranks_a, ranks_b)[0, 1]),
        "top_minus_bottom_bps": float(means[-1] - means[0]),
        "top_bucket_bps": float(means[-1]),
        "bottom_bucket_bps": float(means[0]),
        "n_per_bucket": int(table["n"].median()),
    }
