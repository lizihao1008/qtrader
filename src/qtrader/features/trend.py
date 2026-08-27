"""Trend estimation with a null distribution that actually holds.

Two estimators of the same quantity — the drift, expressed in standard
deviations of what a driftless random walk would have produced:

* :func:`drift_zscore` fits a straight line over a fixed window. Equal weights,
  robust to a single bad print, but it says nothing until the window is full.
* :func:`ewma_drift_zscore` weights exponentially and has no window at all. It
  is defined from the first bar, because its standard error shrinks as evidence
  accumulates rather than being switched on at a threshold.
* :func:`session_drift_zscore` measures the drift since the session opened, so
  its window grows with the day. It is the one that can see a slow move that no
  fixed span resolves.

All three are calibrated the same way, so the same threshold means the same
thing across them — which is what makes them interchangeable in a config.

"There is a clear trend" is a claim about a **drift being distinguishable from
noise**. Making it testable needs two things: an estimator of the drift, and a
scale to judge it against. Getting the second one right is where most trend
filters quietly fail.

The estimator
-------------
Over a trailing window of ``W`` bars, fit by ordinary least squares

    log P_{t-W+1+i} = a + b * i + e_i ,    i = 0 .. W-1

``b`` is the drift per bar. OLS is used rather than the endpoint return
``log(P_t / P_{t-W}) / W`` because it averages over every price in the window
and so is far less sensitive to a single bad print at either end.

The scale — and why the textbook one is wrong here
--------------------------------------------------
The obvious move is the regression's own t-statistic, ``b / se(b)`` with
``se(b) = s / sqrt(Sxx)``. That standard error assumes independent residuals.
Prices are not: a random walk's deviations from a fitted line are strongly
autocorrelated, so ``s`` badly understates the sampling variability of ``b``.
The result is the classic spurious regression — on a **driftless random walk**
that statistic exceeds 2 in absolute value about 80% of the time, so a filter
built on "|t| >= 2 means 5%" is not filtering anything.

So the scale is derived under the correct null instead: increments i.i.d. with
per-bar volatility ``sigma``. Writing ``b = sum_i w_i y_i`` with
``w_i = (i - mean_i) / Sxx``, and ``y_i = y_0 + sum_{k<=i} e_k``, the level term
drops out because ``sum_i w_i = 0``, leaving

    b = sum_k e_k * c_k ,      c_k = sum_{i >= k} w_i
    Var(b) = sigma^2 * sum_k c_k^2

so that

    z = b / (sigma * sqrt(sum_k c_k^2))

is standard normal under a driftless random walk. ``sum_k c_k^2`` depends only
on the window length, so it is computed once per ``W``.

``z`` is then what "clear trend" means: how many standard deviations the drift
is from what a trendless market would have produced. It is dimensionless,
comparable across symbols and volatility regimes, and ``|z| >= 2`` carries its
usual 5% reading — verified by simulation in the tests.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

#: Windows shorter than this cannot support a drift estimate worth having.
MIN_WINDOW = 5


def slope_variance(window: int) -> float:
    """``Sxx`` for a regressor of consecutive integers: ``W(W^2-1)/12``.

    The design's leverage. It grows cubically with the window, which is why a
    longer window can resolve a drift a shorter one cannot.
    """
    return window * (window**2 - 1) / 12.0


@lru_cache(maxsize=32)
def random_walk_slope_scale(window: int) -> float:
    """``sqrt(sum_k c_k^2)`` — the OLS slope's sampling scale per unit sigma.

    Multiply by the per-bar volatility to get the standard deviation of the
    fitted slope when the underlying process is a driftless random walk.
    """
    index = np.arange(window, dtype=float)
    weights = (index - index.mean()) / slope_variance(window)
    # c_k = sum_{i >= k} w_i, for k = 1 .. W-1 (k = 0 contributes the level term,
    # which cancels because the weights sum to zero).
    tail_sums = np.cumsum(weights[::-1])[::-1][1:]
    return float(np.sqrt(np.sum(tail_sums**2)))


def rolling_slope(log_price: pd.DataFrame, window: int) -> pd.DataFrame:
    """OLS drift per bar over a trailing window, in log units.

    Computed from rolling sums of ``y`` and ``n*y``, so the cost is O(1) per bar
    rather than a least-squares fit per window, and the result is exact.
    """
    if window < MIN_WINDOW:
        raise ValueError(f"window must be at least {MIN_WINDOW}, got {window}")

    y = log_price
    n = pd.Series(np.arange(len(y), dtype=float), index=y.index)

    sum_y = y.rolling(window, min_periods=window).sum()
    sum_ny = y.mul(n, axis=0).rolling(window, min_periods=window).sum()

    # Local index i = n - (t - W + 1), so sum(i*y) = sum(n*y) - (t - W + 1)*sum(y).
    window_start = n - (window - 1)
    sum_iy = sum_ny - sum_y.mul(window_start, axis=0)

    sxy = sum_iy - sum_y * ((window - 1) / 2.0)
    return sxy / slope_variance(window)


def drift_zscore(
    log_price: pd.DataFrame,
    window: int,
    *,
    volatility: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Trailing drift in standard deviations of a driftless random walk.

    ``volatility`` is the per-bar standard deviation of log returns; when it is
    not supplied it is estimated over the same window, which keeps the null
    self-contained. Supplying a longer-window estimate makes ``z`` steadier at
    the cost of reacting more slowly to a volatility regime change.
    """
    slope = rolling_slope(log_price, window)
    if volatility is None:
        volatility = log_price.diff().rolling(window, min_periods=window).std()

    scale = volatility * random_walk_slope_scale(window)
    return slope / scale.where(scale > 0)


def ewma_drift_zscore(
    returns: pd.DataFrame,
    span: int,
    *,
    volatility: pd.DataFrame,
    restart: pd.Series | None = None,
) -> pd.DataFrame:
    """Trailing drift in random-walk standard deviations, with no fixed window.

    :func:`drift_zscore` needs ``W`` bars before it says anything, which on an
    intraday strategy that may not look across the overnight gap means an hour
    of every session with no opinion at all. This estimator has no such cliff:
    it weights returns exponentially and lets its **own standard error** account
    for how little history it has seen.

    With ``beta = 1 - 2/(span+1)`` and ``n`` observations since the restart::

        b_hat = sum_k beta^k r_{t-k} / S1 ,   S1 = sum_k beta^k
        Var(b_hat) = sigma^2 * S2 / S1^2 ,    S2 = sum_k beta^(2k)
        z = b_hat * S1 / (sigma * sqrt(S2))
          = (sum_k beta^k r_{t-k}) / (sigma * sqrt(S2))

    Both sums are finite and closed-form, so ``z`` is standard normal under a
    driftless random walk at **every** bar, including the first: early in a
    session ``S2`` is small, so the same raw move produces a smaller ``z``. The
    statistic demands more evidence precisely when it has less — which is the
    behaviour a hard warm-up window was crudely approximating.

    ``restart`` groups the series (typically the session date) so the estimator
    begins again at each session open and sees only that session's action. The
    first bar of each group carries no information — its return is zero by the
    within-session convention (:func:`qtrader.features.relative.bar_log_returns`)
    — so the statistic is undefined there and counts only the bars after it.
    """
    beta = 1.0 - 2.0 / (span + 1.0)
    clean = returns.fillna(0.0)

    if restart is None:
        weighted = clean.ewm(alpha=1.0 - beta, adjust=False).mean() / (1.0 - beta)
        count = pd.Series(np.arange(len(clean), dtype=float), index=clean.index)
    else:
        groups = restart.to_numpy()
        weighted = clean.groupby(groups).transform(
            lambda column: column.ewm(alpha=1.0 - beta, adjust=False).mean()
        ) / (1.0 - beta)
        count = pd.Series(groups, index=clean.index).groupby(groups).cumcount().astype(float)

    # S2 = sum_{k<n} beta^(2k), the variance of the unnormalised sum in units of
    # sigma^2. It is zero on a group's first bar, where there is nothing to say.
    sum_squares = (1.0 - beta ** (2.0 * count)) / (1.0 - beta**2)
    scale = volatility.mul(np.sqrt(sum_squares), axis=0)
    return weighted / scale.where(scale > 0)


def session_drift_zscore(
    returns: pd.DataFrame,
    *,
    volatility: pd.DataFrame,
    restart: pd.Series,
) -> pd.DataFrame:
    """Drift since the session opened, in random-walk standard deviations.

    The simplest of the three, and the only one whose measurement window grows
    with the day::

        z = sum(r since the open) / (sigma * sqrt(n))

    Under a driftless random walk the numerator is ``N(0, n*sigma^2)``, so ``z``
    is standard normal exactly — no window, no span, no parameter at all.

    It exists because a fixed span cannot see a slow, sustained move. A stock
    that grinds 18% higher over five hours may drift only ~1.8 sigma in any
    single hour, so a 60-bar estimator reads noise all day while the session as
    a whole is a 2.3-sigma event. This statistic reads the session as a whole.

    The cost is symmetric: it turns slowly. It is a statement about the day so
    far, not about the last twenty minutes, so it should be paired with an exit
    that does not wait for it (here, the volatility barrier).
    """
    groups = restart.to_numpy()
    clean = returns.fillna(0.0)

    cumulative = clean.groupby(groups).cumsum()
    elapsed = pd.Series(groups, index=clean.index).groupby(groups).cumcount().astype(float)

    scale = volatility.mul(np.sqrt(elapsed), axis=0)
    return cumulative / scale.where(scale > 0)
