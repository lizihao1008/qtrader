"""Market- and sector-relative (residual) features.

The project thesis is that a stock's move *relative to what it should have done*
carries more information than its raw move. A stock up 0.4% while its sector is
up 0.5% has not gone up — it has lagged.

Everything here operates on wide ``timestamp x symbol`` frames from
:class:`qtrader.data.panel.BarPanel` and is causal: rolling windows look only
backwards, and betas at bar ``t`` are estimated from returns up to ``t``.

Log returns are used throughout so that a multi-bar return is the plain sum of
its 1-bar returns, which keeps residual accumulation exact.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date

#: Betas outside this range come from a near-zero reference variance, not from
#: real co-movement; they are clipped rather than allowed to explode.
BETA_LIMIT = 3.0


def bar_log_returns(close: pd.DataFrame, *, within_session: bool = True) -> pd.DataFrame:
    """1-bar log returns for every symbol.

    With ``within_session`` the first bar of each session is 0 instead of the
    overnight gap. The gap is a different phenomenon — it is news, not intraday
    drift — and letting it into a 30-minute residual-momentum window would swamp
    the signal and distort every rolling beta. It belongs in its own
    ``overnight_gap`` feature.
    """
    returns = np.log(close / close.shift(1))
    if within_session:
        day = session_date(close.index).to_numpy()
        first_of_session = pd.Series(day, index=close.index).groupby(day).cumcount() == 0
        returns.loc[first_of_session.to_numpy()] = 0.0
    return returns


def trailing_return(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Cumulative log return over the last ``lookback`` bars."""
    return returns.rolling(lookback, min_periods=lookback).sum()


def align_reference(returns: pd.DataFrame, reference_of: dict[str, str]) -> pd.DataFrame:
    """Wide frame whose column ``s`` holds the returns of ``s``'s reference.

    ``reference_of`` maps each tradable symbol to its benchmark or sector ETF,
    both of which must be columns of ``returns``.
    """
    missing = {ref for ref in reference_of.values() if ref not in returns.columns}
    if missing:
        raise KeyError(f"reference symbols missing from the panel: {sorted(missing)}")
    return pd.DataFrame(
        {symbol: returns[ref] for symbol, ref in reference_of.items()}, index=returns.index
    )


def rolling_beta(
    returns: pd.DataFrame, reference: pd.DataFrame, window: int
) -> pd.DataFrame:
    """Trailing beta of each symbol to its own reference series.

    Computed from rolling moments rather than ``rolling().cov()`` so that each
    column is paired with exactly one reference column instead of every other.
    """
    mean_r = returns.rolling(window, min_periods=window).mean()
    mean_x = reference.rolling(window, min_periods=window).mean()
    covariance = (returns * reference).rolling(window, min_periods=window).mean() - mean_r * mean_x
    variance = (reference**2).rolling(window, min_periods=window).mean() - mean_x**2

    beta = covariance / variance.where(variance > 0)
    return beta.clip(-BETA_LIMIT, BETA_LIMIT)


def residual_returns(
    returns: pd.DataFrame, reference: pd.DataFrame, beta: pd.DataFrame
) -> pd.DataFrame:
    """Return left over after removing the reference's beta-scaled move."""
    return returns - beta * reference


def relative_returns(returns: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Plain difference to the reference — residual returns with beta fixed at 1."""
    return returns - reference
