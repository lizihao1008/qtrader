"""Did the setup predict the outcome?

Two complementary views over a table of episodes:

* :func:`outcome_contrast` — what distinguished winning trades from losing ones,
  feature by feature;
* :func:`quantile_profile` — how the outcome changes as one feature moves,
  which catches monotonic conditioning that a two-group mean comparison misses
  and exposes the far more common case of a non-monotonic fluke.

**This is hypothesis generation, not evidence.** Scanning sixteen features
against one outcome will produce two or three "significant" results from noise
alone; :func:`bonferroni_t_threshold` says how large a t-statistic has to be
before it means anything at that width. Even then, overlapping trades in the
same market minute are not independent observations, so the effective sample is
smaller than the row count suggests. A condition that survives all of this is
still only a candidate until it holds on a window it was not found on.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

DEFAULT_TARGET = "gross_return_bps"


def bonferroni_t_threshold(n_features: int, alpha: float = 0.05) -> float:
    """|t| a single feature must clear when ``n_features`` were screened.

    Normal approximation, which is close enough at these sample sizes and makes
    the point that matters: the bar rises with the width of the search.
    """
    return NormalDist().inv_cdf(1.0 - alpha / (2 * max(n_features, 1)))


def outcome_contrast(
    features: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    outcome: str = "gross_win",
) -> pd.DataFrame:
    """Mean of each feature among winners vs losers, with a Welch t-statistic."""
    winners = features.loc[features[outcome]]
    losers = features.loc[~features[outcome]]
    rows = []

    for column in columns:
        if column not in features.columns:
            continue
        a = winners[column].dropna()
        b = losers[column].dropna()
        if len(a) < 2 or len(b) < 2:
            continue
        difference = float(a.mean() - b.mean())
        standard_error = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        rows.append(
            {
                "feature": column,
                "mean_win": float(a.mean()),
                "mean_loss": float(b.mean()),
                "difference": difference,
                "t_stat": difference / standard_error if standard_error > 0 else np.nan,
                "n_win": len(a),
                "n_loss": len(b),
            }
        )

    table = pd.DataFrame(rows)
    return table.reindex(table["t_stat"].abs().sort_values(ascending=False).index).reset_index(
        drop=True
    )


def quantile_profile(
    features: pd.DataFrame,
    column: str,
    *,
    target: str = DEFAULT_TARGET,
    n_bins: int = 5,
) -> pd.DataFrame:
    """Mean outcome per quantile bin of one feature.

    A real condition shows a monotone gradient across the bins. A single extreme
    bin with ordinary neighbours is usually a handful of trades, not an effect.
    """
    usable = features[[column, target]].dropna()
    if usable[column].nunique() < n_bins:
        return pd.DataFrame()

    bins = pd.qcut(usable[column], n_bins, labels=False, duplicates="drop")
    grouped = usable.groupby(bins)[target]
    profile = pd.DataFrame(
        {
            "low": usable.groupby(bins)[column].min(),
            "high": usable.groupby(bins)[column].max(),
            "mean_target": grouped.mean(),
            "median_target": grouped.median(),
            "win_rate": grouped.apply(lambda s: float((s > 0).mean())),
            "n": grouped.size(),
        }
    )
    profile.index.name = f"{column}_bin"
    return profile


def rank_conditions(
    features: pd.DataFrame,
    columns: tuple[str, ...],
    *,
    target: str = DEFAULT_TARGET,
    n_bins: int = 5,
) -> pd.DataFrame:
    """Screen every feature against the outcome, strongest apparent effect first.

    ``t_rho`` is the t-statistic of the Spearman correlation; ``bin_spread`` is
    the difference in mean outcome between the top and bottom quantile bins.
    Read the two together: a large spread with a small ``t_rho`` means one bin
    moved, not the feature.
    """
    rows = []
    for column in columns:
        if column not in features.columns:
            continue
        usable = features[[column, target]].dropna()
        n = len(usable)
        if n < 4 * n_bins or usable[column].nunique() < n_bins:
            continue

        # Spearman is Pearson on the ranks; computing it that way avoids
        # pulling in SciPy for one correlation (CLAUDE.md §21).
        rho = float(usable[column].rank().corr(usable[target].rank()))
        t_rho = rho * np.sqrt((n - 2) / (1 - rho**2)) if abs(rho) < 1 else np.nan

        profile = quantile_profile(usable, column, target=target, n_bins=n_bins)
        spread = float(profile["mean_target"].iloc[-1] - profile["mean_target"].iloc[0])
        gradient = profile["mean_target"].is_monotonic_increasing or (
            profile["mean_target"].is_monotonic_decreasing
        )

        rows.append(
            {
                "feature": column,
                "spearman_rho": rho,
                "t_rho": t_rho,
                "bin_spread": spread,
                "monotone": bool(gradient),
                "top_bin_mean": float(profile["mean_target"].iloc[-1]),
                "bottom_bin_mean": float(profile["mean_target"].iloc[0]),
                "n": n,
            }
        )

    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.reindex(table["t_rho"].abs().sort_values(ascending=False).index).reset_index(
        drop=True
    )
