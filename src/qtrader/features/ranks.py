"""Cross-sectional transforms.

At each timestamp these compare symbols *against each other*, which is the shape
the production decision takes: not "is AAPL attractive?" but "is AAPL the most
attractive thing available right now?".

The ``eligible`` mask is mandatory rather than optional. Ranking over symbols
that could not actually be traded at that bar — no price, stale quote, too
illiquid — silently invents opportunities, and ranking over a universe that was
chosen with hindsight is the classic survivorship leak (CLAUDE.md §11.1).
"""

from __future__ import annotations

import pandas as pd


def _mask(values: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    aligned = eligible.reindex(index=values.index, columns=values.columns, fill_value=False)
    return values.where(aligned)


def cross_sectional_rank(values: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    """Percentile rank in ``[0, 1]`` across eligible symbols at each timestamp."""
    return _mask(values, eligible).rank(axis=1, pct=True)


def cross_sectional_percentile(
    values: pd.DataFrame, eligible: pd.DataFrame
) -> pd.DataFrame:
    """Position in ``[0, 1]``, with the worst symbol at exactly 0 and the best at 1.

    :func:`cross_sectional_rank` returns ``rank / N``, whose smallest value is
    ``1/N`` rather than 0. On a universe where a typical bar has nine eligible
    symbols that floor is 0.111, so a symmetric pair of gates like
    ``rank > 0.95`` / ``rank < 0.05`` is not symmetric at all: the long gate is
    reachable and the short gate is unreachable on 99% of bars. Rescaling by
    ``(rank - 1) / (N - 1)`` makes the two ends mirror images, which is what a
    long/short rule assumes it is getting.

    A single eligible symbol has no cross-section to be ranked in and is NaN,
    not 0 or 1.
    """
    masked = _mask(values, eligible)
    ordinal = masked.rank(axis=1, method="average")
    count = masked.notna().sum(axis=1)
    return ordinal.sub(1.0).div((count - 1).where(count > 1), axis=0)


def cross_sectional_zscore(values: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    """Standardised deviation from the cross-sectional mean at each timestamp."""
    masked = _mask(values, eligible)
    centered = masked.sub(masked.mean(axis=1), axis=0)
    spread = masked.std(axis=1, ddof=1)
    return centered.div(spread.where(spread > 0), axis=0)


def cross_sectional_count(eligible: pd.DataFrame) -> pd.Series:
    """How many symbols were tradable at each timestamp."""
    return eligible.sum(axis=1).rename("n_eligible")
