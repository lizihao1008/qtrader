"""Two designs the curated event/control test cannot answer.

`trend_precursor` compares the bar before a Trend Start against controls drawn
at least ``exclusion_minutes`` from **any** Trend Start. That pool is selected on
the outcome: every bar near a trend has been removed from it, and bars near a
trend are exactly the ones with unusual pre-trend behaviour. A difference
between the two groups is therefore part signal and part sampling, in unknown
proportion, and the split cannot be recovered afterwards.

Two designs here, neither of which has that freedom.

**Unconditional lift** (:func:`unconditional_lift`) scores *every* bar with a
valid factor and a complete label window. No control pool exists, so nothing can
be selected. The base rate is the real one — a trend start is rare — and the
question becomes the one a trader asks: of the bars in the top decile of this
factor, what fraction actually start a trend, against the unconditional rate?

**Hard negatives** (:func:`hard_negative_contrast`) asks the sharper question.
Among bars that already *look* like a breakout — top decile of the factor — what
separates the ones that run from the ones that fail? A factor can look strong
against quiet controls and be useless here, and this is the comparison that
decides whether it is tradeable.

Both are descriptive. Nothing is fitted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.sessions import session_date


@dataclass(frozen=True)
class LiftResult:
    base_rate: float
    n_bars: int
    n_events: int
    table: pd.DataFrame


def label_trend_starts(index: pd.DatetimeIndex, events: pd.Series) -> np.ndarray:
    """1 where a Trend Start's own bar opens, else 0."""
    starts = pd.DatetimeIndex(events).tz_convert(index.tz)
    return index.isin(starts).astype(int)


def _decile_lift(factor: np.ndarray, label: np.ndarray, n_bins: int) -> pd.DataFrame:
    ok = np.isfinite(factor)
    f, y = factor[ok], label[ok]
    if len(f) < n_bins * 20 or y.sum() == 0:
        return pd.DataFrame()
    bins = pd.qcut(pd.Series(f), n_bins, labels=False, duplicates="drop")
    frame = pd.DataFrame({"bin": bins.to_numpy(), "y": y})
    grouped = frame.groupby("bin")["y"]
    rate = grouped.mean()
    base = y.mean()
    return pd.DataFrame({
        "n": grouped.size(), "rate": rate, "lift": rate / base,
    })


def _auc(factor: np.ndarray, label: np.ndarray) -> float:
    ok = np.isfinite(factor)
    f, y = factor[ok], label[ok]
    positives, negatives = y.sum(), len(y) - y.sum()
    if positives == 0 or negatives == 0:
        return float("nan")
    order = pd.Series(f).rank().to_numpy()
    return float((order[y == 1].sum() - positives * (positives + 1) / 2)
                 / (positives * negatives))


def unconditional_lift(
    close: pd.Series,
    events: pd.Series,
    *,
    windows=(1, 3, 5, 10, 20),
    leads=(1, 3, 5, 10),
    n_bins: int = 10,
    directional: bool = True,
) -> LiftResult:
    """Every bar is a sample. ``directional`` keeps the factor's sign."""
    index = pd.DatetimeIndex(close.index)
    label = label_trend_starts(index, events)
    day = pd.Series(session_date(index).to_numpy(), index=index)
    logged = np.log(close.astype(float))

    rows = []
    for window in windows:
        for lead in leads:
            # Ends at close[t - lead], strictly before the event bar opens, and
            # never reaching across a session boundary.
            raw = (logged.shift(lead) - logged.shift(lead + window))
            same = day.shift(lead + window).to_numpy() == day.to_numpy()
            factor = raw.where(same).to_numpy(dtype=float)
            values = factor if directional else np.abs(factor)
            table = _decile_lift(values, label, n_bins)
            if table.empty:
                continue
            rows.append({
                "window": window, "lead": lead,
                "auc": _auc(values, label),
                "top_lift": float(table["lift"].iloc[-1]),
                "bottom_lift": float(table["lift"].iloc[0]),
                "max_lift": float(table["lift"].max()),
                "spearman_bins": float(
                    np.corrcoef(np.arange(len(table)),
                                pd.Series(table["lift"]).rank())[0, 1]
                ),
            })
    return LiftResult(
        base_rate=float(label.mean()), n_bars=int(np.isfinite(logged).sum()),
        n_events=int(label.sum()), table=pd.DataFrame(rows),
    )


def hard_negative_contrast(
    close: pd.Series,
    events: pd.Series,
    *,
    window: int = 5,
    lead: int = 1,
    top_quantile: float = 0.9,
    n_bins: int = 5,
    extra: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    """Among bars that already look like a breakout, what separates the runners?

    Restricts to the top ``top_quantile`` of |factor| and then asks whether any
    other measurement orders the outcome inside that slice. If nothing does, a
    breakout filter built on these inputs cannot work, however good the factor
    looks against quiet controls.
    """
    index = pd.DatetimeIndex(close.index)
    label = label_trend_starts(index, events)
    day = pd.Series(session_date(index).to_numpy(), index=index)
    logged = np.log(close.astype(float))
    raw = logged.shift(lead) - logged.shift(lead + window)
    same = day.shift(lead + window).to_numpy() == day.to_numpy()
    factor = raw.where(same)

    magnitude = factor.abs()
    cut = magnitude.quantile(top_quantile)
    selected = magnitude >= cut
    y = label[selected.fillna(False).to_numpy()]
    if y.sum() == 0:
        return pd.DataFrame()

    rows = [{
        "measurement": "(all bars)", "n": int(len(label)),
        "rate": float(label.mean()), "lift": 1.0, "spearman_bins": np.nan,
    }, {
        "measurement": f"|mom_{window}| top {1 - top_quantile:.0%}",
        "n": int(len(y)), "rate": float(y.mean()),
        "lift": float(y.mean() / label.mean()), "spearman_bins": np.nan,
    }]
    for name, series in (extra or {}).items():
        values = series.reindex(index).where(selected).to_numpy(dtype=float)
        table = _decile_lift(values, label, n_bins)
        if table.empty:
            continue
        rows.append({
            "measurement": f"  ...then {name}",
            "n": int(table["n"].sum()),
            "rate": float(table["rate"].iloc[-1]),
            "lift": float(table["lift"].iloc[-1]),
            "spearman_bins": float(
                np.corrcoef(np.arange(len(table)), pd.Series(table["lift"]).rank())[0, 1]
            ),
        })
    return pd.DataFrame(rows)
