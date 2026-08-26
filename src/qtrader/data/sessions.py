"""US equity session logic.

Timezone policy (see docs/context/CONTEXT.md):

* bars are **stored and processed in UTC**;
* anything that depends on the trading day — regular-hours filtering, intraday
  VWAP resets, end-of-day flattening — converts to ``America/New_York`` here
  and nowhere else.

Exchange holidays are not modelled: on a holiday the provider simply returns no
bars, so a holiday behaves like an empty session.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

MARKET_TZ = "America/New_York"

#: Regular trading hours, exchange-local, half-open interval [open, close).
RTH_OPEN = dt.time(9, 30)
RTH_CLOSE = dt.time(16, 0)


def to_market_time(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Convert a UTC bar index to exchange-local time."""
    return index.tz_convert(MARKET_TZ)


def regular_hours_mask(index: pd.DatetimeIndex) -> pd.Series:
    """Boolean mask selecting bars inside regular trading hours."""
    local = to_market_time(index)
    weekday = local.dayofweek < 5
    in_hours = (local.time >= RTH_OPEN) & (local.time < RTH_CLOSE)
    return pd.Series(weekday & in_hours, index=index)


def filter_regular_hours(bars: pd.DataFrame) -> pd.DataFrame:
    """Drop pre-market, after-hours and weekend bars."""
    return bars.loc[regular_hours_mask(bars.index).to_numpy()]


def session_date(index: pd.DatetimeIndex) -> pd.Series:
    """Exchange-local calendar date of each bar — the intraday grouping key."""
    return pd.Series(to_market_time(index).date, index=index, name="session_date")


def at_or_after_market_time(index: pd.DatetimeIndex, cutoff: dt.time) -> pd.Series:
    """Mark bars at or after an exchange-local wall-clock time.

    Purely calendar/clock based, so it is causal by construction and behaves
    identically in a backtest and in live trading. Intraday strategies use it
    to stop opening new risk and to flatten before the close.
    """
    local = to_market_time(index)
    return pd.Series(local.time >= cutoff, index=index)
