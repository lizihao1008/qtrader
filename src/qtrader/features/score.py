"""Combine several factors into one score.

:func:`weighted_score` standardises *across symbols at each bar* before it
weights. That is the right object for a stock universe: a raw 15-minute return
and a raw relative-volume ratio are not in the same units, and averaging them
directly would silently weight whichever happens to have the larger spread.

:func:`combine_score` skips that step. Use it when the factors are already in
the same units (random-walk sigmas) and a single name is a valid universe.

The score is then ranked cross-sectionally as well, so a rule can ask both
"how extreme is this, in standard deviations" and "how extreme is this, relative
to everything else available right now". The two disagree exactly when the whole
universe is moving together, which is the case a percentile gate is there to
catch.
"""

from __future__ import annotations

import pandas as pd

from .ranks import cross_sectional_percentile, cross_sectional_zscore


def factor_zscores(
    factors: dict[str, pd.DataFrame], eligible: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Cross-sectional z-score of every factor, on the eligible universe only."""
    return {
        name: cross_sectional_zscore(frame, eligible) for name, frame in factors.items()
    }


def weighted_score(
    factors: dict[str, pd.DataFrame],
    weights: dict[str, float],
    eligible: pd.DataFrame,
    *,
    min_factors: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """``(score, percentile_rank, per-factor z-scores)``.

    Missing factors are skipped rather than treated as zero — a NaN means "not
    measured yet", and scoring it as an average reading would let a symbol enter
    on a score built from half its inputs. ``min_factors`` is how many must be
    present for the bar to carry a score at all; the weights of the factors that
    are present are renormalised so a score is always on the same scale.

    Weights are not required to be positive or to sum to anything. A negative
    weight is a deliberate statement that the factor is expected to invert, and
    the caller owns that claim.
    """
    unknown = set(weights) - set(factors)
    if unknown:
        raise KeyError(f"weights name factors that were not supplied: {sorted(unknown)}")
    if not weights:
        raise ValueError("weighted_score needs at least one weighted factor")

    scores = factor_zscores({k: factors[k] for k in weights}, eligible)
    total = None
    used = None
    for name, weight in weights.items():
        z = scores[name]
        present = z.notna()
        contribution = z.fillna(0.0) * weight
        total = contribution if total is None else total + contribution
        magnitude = present.astype(float) * abs(weight)
        used = magnitude if used is None else used + magnitude

    enough = sum(z.notna().astype(int) for z in scores.values()) >= min_factors
    score = (total / used.where(used > 0)).where(enough)
    # The symmetric percentile, not rank/N: see cross_sectional_percentile
    # for why a long/short pair of gates is otherwise one-sided.
    return score, cross_sectional_percentile(score, eligible), scores


def combine_score(
    factors: dict[str, pd.DataFrame],
    weights: dict[str, float],
    *,
    min_factors: int = 1,
) -> pd.DataFrame:
    """Weighted mean of factors that are *already in the same units*.

    No cross-sectional step. A single-name series is a valid input — that is
    the point of a broad-index score. Missing values are skipped and the
    remaining weights renormalised, same rule as :func:`weighted_score`.
    """
    unknown = set(weights) - set(factors)
    if unknown:
        raise KeyError(f"weights name factors that were not supplied: {sorted(unknown)}")
    if not weights:
        raise ValueError("combine_score needs at least one weighted factor")

    total = None
    used = None
    present_count = None
    for name, weight in weights.items():
        frame = factors[name]
        present = frame.notna()
        contribution = frame.fillna(0.0) * weight
        total = contribution if total is None else total + contribution
        magnitude = present.astype(float) * abs(weight)
        used = magnitude if used is None else used + magnitude
        present_count = (
            present.astype(int)
            if present_count is None
            else present_count + present.astype(int)
        )

    enough = present_count >= min_factors
    return (total / used.where(used > 0)).where(enough)
