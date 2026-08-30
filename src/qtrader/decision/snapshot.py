"""The evidence a candidate is judged on, as of its decision bar.

Everything here is read at or before ``candidate.position``. The slice is the
whole anti-lookahead guarantee for this layer, so it happens in one place and is
asserted in one test.

Two timeframes, as the brief requires:

* **execution context** — the bars the strategy itself decided on;
* **higher-timeframe context** — the same window resampled coarser, so the model
  is told what the move looks like when the noise is aggregated away.

Resampling here is *backward-looking only*: a coarse bar is emitted only when
its whole interval has closed at or before the decision bar. A partial final bar
would show the model a candle that had not finished forming.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.sessions import session_date, to_market_time

#: Execution-context bars shown to the model.
DEFAULT_WINDOW = 60

#: How many execution bars make one higher-timeframe bar.
DEFAULT_COARSE_FACTOR = 6


@dataclass(frozen=True)
class Snapshot:
    """One candidate's evidence: prices, the strategy's own indicators, session state."""

    symbol: str
    timestamp: pd.Timestamp
    direction: int
    bars: pd.DataFrame              # execution-timeframe OHLCV, ending at the decision bar
    coarse: pd.DataFrame            # higher-timeframe view of the same window
    indicators: dict                # the strategy's own values at the decision bar
    session: dict                   # where the bar sits in its session
    sufficient: bool = True
    reason: str = ""
    features: dict = field(default_factory=dict)

    def as_record(self) -> dict:
        """Flat, JSON-safe view for the journal."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction,
            "bars": len(self.bars),
            "indicators": _jsonable(self.indicators),
            "session": _jsonable(self.session),
            "features": _jsonable(self.features),
            "sufficient": self.sufficient,
            "reason": self.reason,
        }


def build_snapshot(
    candidate,
    panel,
    indicators: dict[str, pd.DataFrame],
    *,
    window: int = DEFAULT_WINDOW,
    coarse_factor: int = DEFAULT_COARSE_FACTOR,
) -> Snapshot:
    """Everything observable about ``candidate`` at the bar it was decided on."""
    at = candidate.position
    symbol = candidate.symbol
    index = panel.index

    if at < 1:
        return _insufficient(candidate, "no history before the decision bar")

    start = max(at - window + 1, 0)
    # Inclusive of the decision bar, exclusive of everything after it.
    bars = panel.bars(symbol).reindex(index[start : at + 1])
    usable = bars[["open", "high", "low", "close"]].dropna()
    if len(usable) < max(coarse_factor, 5):
        return _insufficient(candidate, f"only {len(usable)} printed bars in the window")

    frame = indicators.get(symbol)
    at_decision = {}
    if frame is not None:
        row = frame.iloc[at]
        at_decision = {name: row[name] for name in frame.columns if _finite(row[name])}

    return Snapshot(
        symbol=symbol,
        timestamp=index[at],
        direction=candidate.direction,
        bars=bars,
        coarse=coarsen(bars, coarse_factor),
        indicators=at_decision,
        session=_session_state(panel, symbol, index, at),
        features=_derived(bars),
    )


def coarsen(bars: pd.DataFrame, factor: int) -> pd.DataFrame:
    """Aggregate ``factor`` execution bars into one, counting back from the last.

    Counting back rather than forward matters: it guarantees the final coarse bar
    ends exactly at the decision bar. Grouping forward from the window's start
    would leave a partial, still-forming bar at the end — a candle the model
    would read as complete.
    """
    if factor <= 1 or bars.empty:
        return bars

    groups = (np.arange(len(bars))[::-1] // factor)[::-1]
    whole = bars.groupby(groups).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
    )
    # The oldest group may be short; that is a truncated *past*, which is
    # harmless, unlike a truncated present.
    whole.index = [bars.index[np.flatnonzero(groups == g)[-1]] for g in whole.index]
    return whole


def _session_state(panel, symbol: str, index, at: int) -> dict:
    day = session_date(index).to_numpy()
    first = at - int(np.flatnonzero(day[: at + 1] == day[at])[0] == 0 and 0)
    same = np.flatnonzero(day == day[at])
    first = int(same[0])

    close = panel.close[symbol]
    price = close.iat[at]
    opened = close.iat[first]
    session_high = float(np.nanmax(panel.field("high")[symbol].to_numpy()[first : at + 1]))
    session_low = float(np.nanmin(panel.field("low")[symbol].to_numpy()[first : at + 1]))
    local = to_market_time(index[at : at + 1])[0]

    return {
        "clock": local.strftime("%H:%M"),
        "bar_of_session": at - first,
        "session_high": session_high,
        "session_low": session_low,
        "drift_since_open_bps": float(np.log(price / opened) * 1e4)
        if _finite(price) and _finite(opened) and opened > 0 else None,
        "position_in_session_range": float((price - session_low) / (session_high - session_low))
        if session_high > session_low else None,
    }


def _derived(bars: pd.DataFrame) -> dict:
    """A few scale-free descriptions of the recent window, for the prompt."""
    close = bars["close"].dropna()
    if len(close) < 5:
        return {}
    returns = np.log(close / close.shift(1)).dropna()
    volume = bars["volume"].dropna()
    recent = min(6, len(close) - 1)
    return {
        "last_close": float(close.iloc[-1]),
        "window_return_bps": float(np.log(close.iloc[-1] / close.iloc[0]) * 1e4),
        "recent_return_bps": float(np.log(close.iloc[-1] / close.iloc[-1 - recent]) * 1e4),
        "bar_volatility_bps": float(returns.std() * 1e4) if len(returns) > 1 else None,
        "window_high": float(bars["high"].max()),
        "window_low": float(bars["low"].min()),
        "volume_vs_window_median": float(volume.iloc[-1] / volume.median())
        if len(volume) and volume.median() > 0 else None,
    }


def _insufficient(candidate, reason: str) -> Snapshot:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return Snapshot(
        symbol=candidate.symbol, timestamp=candidate.timestamp,
        direction=candidate.direction, bars=empty, coarse=empty,
        indicators={}, session={}, sufficient=False, reason=reason,
    )


def _finite(value) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _jsonable(payload: dict) -> dict:
    out = {}
    for key, value in payload.items():
        if value is None or isinstance(value, (str, bool)):
            out[key] = value
        elif _finite(value):
            out[key] = round(float(value), 6)
    return out
