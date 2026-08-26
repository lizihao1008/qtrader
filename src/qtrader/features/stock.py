"""Single-symbol price/volume features.

**Availability rule.** Every function here is *causal*: the value at index ``t``
uses bars up to and including ``t`` and nothing after it. A feature computed on
bar ``t`` is therefore known at that bar's close, which is exactly when a
strategy is allowed to act on it (the backtest engine then executes on the next
bar — see :mod:`qtrader.backtest.engine`).

Centered windows, ``bfill``, and whole-sample normalisation are forbidden in
this module; they are the classic leakage sources listed in CLAUDE.md §11.1.
"""

from __future__ import annotations

import pandas as pd

from ..data.sessions import session_date


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average of the last ``window`` closes (unit: price)."""
    return close.rolling(window, min_periods=window).mean().rename(f"sma_{window}")


def ema(close: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (unit: price)."""
    return close.ewm(span=span, adjust=False).mean().rename(f"ema_{span}")


def macd(
    close: pd.Series,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line and histogram (unit: price)."""
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": line, "macd_signal": signal_line, "macd_hist": line - signal_line}
    )


def log_return(close: pd.Series, periods: int = 1) -> pd.Series:
    """Trailing log return over ``periods`` bars (unit: dimensionless)."""
    import numpy as np

    return pd.Series(
        np.log(close / close.shift(periods)), index=close.index, name=f"log_return_{periods}"
    )


def realized_volatility(close: pd.Series, window: int) -> pd.Series:
    """Rolling std of 1-bar log returns (unit: per-bar return)."""
    return (
        log_return(close, 1)
        .rolling(window, min_periods=window)
        .std()
        .rename(f"realized_vol_{window}")
    )


def intraday_vwap(bars: pd.DataFrame) -> pd.Series:
    """Session-to-date VWAP, reset at each new trading day (unit: price).

    Uses the bar's typical price ``(h + l + c) / 3`` weighted by volume, so it
    only ever consumes bars already closed within the same session.
    """
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    day = session_date(bars.index)
    dollar = (typical * bars["volume"]).groupby(day.to_numpy()).cumsum()
    shares = bars["volume"].groupby(day.to_numpy()).cumsum()
    return (dollar / shares.replace(0.0, pd.NA)).astype("float64").rename("intraday_vwap")
