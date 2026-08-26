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


def cross_sectional_zscore(values: pd.DataFrame, eligible: pd.DataFrame) -> pd.DataFrame:
    """Standardised deviation from the cross-sectional mean at each timestamp."""
    masked = _mask(values, eligible)
    centered = masked.sub(masked.mean(axis=1), axis=0)
    spread = masked.std(axis=1, ddof=1)
    return centered.div(spread.where(spread > 0), axis=0)


def cross_sectional_count(eligible: pd.DataFrame) -> pd.Series:
    """How many symbols were tradable at each timestamp."""
    return eligible.sum(axis=1).rename("n_eligible")
