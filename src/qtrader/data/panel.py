"""Aligned multi-symbol bar data.

Cross-sectional work needs every symbol on one timestamp grid: to rank stocks
against each other at time ``t`` you must know what each of them looked like at
exactly ``t``. :class:`BarPanel` builds that grid from per-symbol canonical bar
frames and exposes each OHLCV field as a wide ``timestamp x symbol`` frame.

Missing bars
------------
A minute with no prints is not missing data — it means nothing traded. Such
bars are filled **within their own session** with the last known close
(``open = high = low = close``) and ``volume = 0``. Forward filling only ever
looks backwards, so it introduces no lookahead; it is reset at each session
boundary so an overnight gap never leaks yesterday's price into today's open.

Two masks describe what actually happened:

``available``  a price is known for this symbol at this bar (it has traded at
               least once earlier in the session)
``traded``     a real bar arrived — the symbol printed volume in this minute

Anything that must not act on a stale quote reads ``traded``, not ``available``
(see :mod:`qtrader.universe.filters`).
"""

from __future__ import annotations

import pandas as pd

from .schema import BAR_COLUMNS
from .sessions import session_date

OHLC = ("open", "high", "low", "close")


class BarPanel:
    """Several symbols' bars aligned on a shared timestamp index."""

    def __init__(self, fields: dict[str, pd.DataFrame], traded: pd.DataFrame):
        self._fields = fields
        self._traded = traded

    # ----------------------------------------------------------- construction
    @classmethod
    def from_frames(cls, frames: dict[str, pd.DataFrame]) -> "BarPanel":
        """Align per-symbol canonical bar frames onto their union index."""
        if not frames:
            raise ValueError("cannot build a panel from zero symbols")

        indexes = [bars.index for bars in frames.values()]
        index = indexes[0]
        for other in indexes[1:]:
            index = index.union(other)
        index = pd.DatetimeIndex(index, name="timestamp")
        day = session_date(index).to_numpy()

        symbols = sorted(frames)
        traded = pd.DataFrame(
            {s: frames[s]["volume"].reindex(index).notna() for s in symbols}, index=index
        )

        close = pd.DataFrame(
            {s: frames[s]["close"].reindex(index) for s in symbols}, index=index
        ).groupby(day).ffill()

        fields: dict[str, pd.DataFrame] = {"close": close}
        for name in ("open", "high", "low"):
            raw = pd.DataFrame(
                {s: frames[s][name].reindex(index) for s in symbols}, index=index
            )
            # An untraded minute has no range: it sits at the last known close.
            fields[name] = raw.where(traded, close)
        for name in ("volume", "trade_count"):
            if all(name in frames[s].columns for s in symbols):
                raw = pd.DataFrame(
                    {s: frames[s][name].reindex(index) for s in symbols}, index=index
                )
                fields[name] = raw.fillna(0.0)

        return cls(fields, traded)

    def replace_field(self, name: str, frame: pd.DataFrame) -> "BarPanel":
        """A copy with one field replaced, for audits that rewrite history.

        Used by the look-ahead audit, which needs to answer "what would this run
        have done if later bars had been different" without reaching into the
        panel's internals.
        """
        if name not in self._fields:
            raise KeyError(f"panel has no field {name!r}; available: {sorted(self._fields)}")
        fields = dict(self._fields)
        fields[name] = frame.reindex(index=self.index, columns=list(self.symbols))
        return BarPanel(fields, self._traded)

    def subset(self, symbols: list[str]) -> "BarPanel":
        """A panel restricted to ``symbols`` (same index)."""
        keep = [s for s in symbols if s in self.symbols]
        if not keep:
            raise ValueError(f"none of {symbols} are in the panel")
        return BarPanel({k: v[keep] for k, v in self._fields.items()}, self._traded[keep])

    # ------------------------------------------------------------------ views
    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._fields["close"].columns)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self._fields["close"].index

    def field(self, name: str) -> pd.DataFrame:
        """Wide ``timestamp x symbol`` frame for one OHLCV field."""
        if name not in self._fields:
            raise KeyError(f"panel has no field {name!r}; available: {sorted(self._fields)}")
        return self._fields[name]

    @property
    def close(self) -> pd.DataFrame:
        return self._fields["close"]

    @property
    def volume(self) -> pd.DataFrame:
        return self._fields["volume"]

    @property
    def traded(self) -> pd.DataFrame:
        """True where a real bar arrived (the symbol printed volume)."""
        return self._traded

    @property
    def available(self) -> pd.DataFrame:
        """True where a price is known — the symbol has traded earlier today."""
        return self.close.notna()

    def bars(self, symbol: str) -> pd.DataFrame:
        """Rebuild one symbol's canonical bar frame, for charts and per-symbol features."""
        data = {
            name: self._fields[name][symbol]
            for name in BAR_COLUMNS
            if name in self._fields
        }
        return pd.DataFrame(data, index=self.index).dropna(subset=["close"])

    def __len__(self) -> int:
        return len(self.index)

    def __repr__(self) -> str:
        return f"BarPanel({len(self.symbols)} symbols, {len(self)} bars)"
