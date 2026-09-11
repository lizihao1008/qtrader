"""Cross-sectional intraday momentum score on a 1-minute grid.

Eight factors are measured per symbol per minute, each standardised *across the
eligible universe at that minute*, then combined with configurable weights into
one ``score``. The score is also ranked cross-sectionally, and an entry needs
both: extreme in absolute terms (``score``) and extreme relative to everything
else available (``rank``). The two disagree precisely when the whole universe
moves together, which is when a z-score alone would fire on all of it at once.

Timing
------
Every factor at bar ``t`` is built from bars ``<= t``. The decision is taken at
``t``'s close and the engine fills it at ``t + execution_lag`` (ADR-0002); this
class never shifts anything forward itself.

State machine, per symbol
-------------------------
* **flat** -> long when ``score > entry_long`` and ``rank > rank_long``;
  short on the mirrored condition.
* **long** -> flat when ``score < exit_long``, the ATR stop is touched, the
  holding limit is reached, or the session is closing.
* A position that closes on a signal reversal does **not** open the other way on
  the same bar. Reversing instantly makes the exit and the entry the same
  decision, so a single noisy bar would both close a position and pay to open
  its opposite; the symbol becomes eligible again on the next bar.

Known prior, recorded so results are read against it rather than in a vacuum:
R04/R05 measured intraday momentum on this data as reliably wrong-signed (rank
IC -0.023, t = -11.2), and R24 §1 found the strongest cross-sectional rankers to
be volatility and size rather than any return. Leaving the return factors
un-normalised (the default, and what the specification asks for) therefore makes
part of this score a volatility ranking; ``volatility_normalise`` exists to
measure how much.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import numpy as np
import pandas as pd

from ..data.sessions import at_or_after_market_time, session_date
from ..features.efficiency import signed_efficiency_ratio
from ..features.levels import average_true_range
from ..features.ranks import cross_sectional_zscore
from ..features.relative import align_reference, bar_log_returns, trailing_return
from ..features.score import weighted_score
from ..features.seasonality import seasonal_volume_ratio
from ..features.stock import intraday_vwap
from .base import MarketContext, Strategy, StrategySignals

FLAT, LONG, SHORT = 0, 1, -1

#: Factor names, fixed so a config cannot invent one silently.
FACTORS = (
    "ret_1m", "ret_5m", "ret_15m", "ret_30m",
    "vwap_deviation", "relative_ret_15m", "rvol_5m", "er_15m",
)

#: Factors with no direction of their own. Relative volume is large whether
#: price rose or fell, so adding it to a signed score with a positive weight
#: pushes every busy symbol towards "long" regardless of which way it moved.
#: It belongs as a confirmation gate (``min_rvol_z``), not as a term in the sum.
UNSIGNED_FACTORS = frozenset({"rvol_5m"})

DEFAULT_WEIGHTS: dict[str, float] = {
    name: 1.0 for name in FACTORS if name not in UNSIGNED_FACTORS
}


class XSecMomentumStrategy(Strategy):
    """Rank-based intraday momentum, one decision per minute."""

    name = "xsec_momentum"

    def __init__(
        self,
        *,
        # --- factor windows, in bars (1-minute grid)
        ret_windows: tuple[int, int, int, int] = (1, 5, 15, 30),
        relative_window: int = 15,
        rvol_window: int = 5,
        er_window: int = 15,
        vol_window: int = 30,
        atr_window: int = 30,
        volatility_normalise: bool = False,
        weights: Mapping[str, float] | None = None,
        min_factors: int = 4,
        min_rvol_z: float | None = None,
        # --- entry / exit thresholds
        entry_long: float = 1.5,
        entry_short: float = -1.5,
        exit_long: float = 0.5,
        exit_short: float = -0.5,
        rank_long: float = 0.95,
        rank_short: float = 0.05,
        max_holding_bars: int = 30,
        stop_atr: float | None = 2.0,
        # --- book
        max_positions: int = 5,
        max_weight: float = 0.2,
        allow_long: bool = True,
        allow_short: bool = True,
        # --- session discipline
        no_entry_before: str | None = "09:35",
        no_entry_after: str | None = "15:30",
        flat_time: str | None = "15:50",
    ):
        if not allow_long and not allow_short:
            raise ValueError("at least one of allow_long / allow_short must be enabled")
        if entry_long <= exit_long:
            raise ValueError("entry_long must be above exit_long")
        if entry_short >= exit_short:
            raise ValueError("entry_short must be below exit_short")
        if not 0.0 <= rank_short < rank_long <= 1.0:
            raise ValueError("need 0 <= rank_short < rank_long <= 1")
        if max_holding_bars < 1:
            raise ValueError("max_holding_bars must be at least 1")
        if stop_atr is not None and stop_atr <= 0:
            raise ValueError("stop_atr must be positive")
        if max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if not 0.0 < max_weight <= 1.0:
            raise ValueError("max_weight must lie in (0, 1]")
        if min_factors < 1:
            raise ValueError("min_factors must be at least 1")

        self.ret_windows = tuple(int(w) for w in ret_windows)
        if len(self.ret_windows) != 4 or any(w < 1 for w in self.ret_windows):
            raise ValueError("ret_windows must be four positive bar counts")
        self.relative_window = int(relative_window)
        self.rvol_window = int(rvol_window)
        self.er_window = int(er_window)
        self.vol_window = int(vol_window)
        self.atr_window = int(atr_window)
        self.volatility_normalise = bool(volatility_normalise)
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        unknown = set(self.weights) - set(FACTORS)
        if unknown:
            raise ValueError(f"unknown factors in weights: {sorted(unknown)}")
        signed = set(self.weights) & UNSIGNED_FACTORS
        if signed:
            raise ValueError(
                f"{sorted(signed)} have no direction and cannot carry a weight "
                "in a signed score; use min_rvol_z as a gate instead"
            )
        self.min_factors = int(min_factors)
        # An unsigned confirmation: the move must also be carrying volume.
        # Applied as a gate on both sides, so it cannot bias the direction.
        self.min_rvol_z = None if min_rvol_z is None else float(min_rvol_z)

        self.entry_long = float(entry_long)
        self.entry_short = float(entry_short)
        self.exit_long = float(exit_long)
        self.exit_short = float(exit_short)
        self.rank_long = float(rank_long)
        self.rank_short = float(rank_short)
        self.max_holding_bars = int(max_holding_bars)
        self.stop_atr = None if stop_atr is None else float(stop_atr)

        self.max_positions = int(max_positions)
        self.max_weight = float(max_weight)
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)

        self.no_entry_before = no_entry_before
        self.no_entry_after = no_entry_after
        self.flat_time = flat_time

    # ---------------------------------------------------------------- signal
    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        panel = context.panel
        close = panel.close[symbols]
        eligible = context.tradable[symbols]

        factors, extras = self._factors(context, symbols)
        score, rank, per_factor = weighted_score(
            factors, self.weights, eligible, min_factors=self.min_factors
        )
        confirmation = cross_sectional_zscore(factors["rvol_5m"], eligible)

        weights = self._walk(
            context, symbols, close, score, rank, extras["atr"], confirmation
        )

        indicators = {
            symbol: pd.DataFrame(
                {
                    "score": score[symbol],
                    "score_rank": rank[symbol],
                    "atr": extras["atr"][symbol],
                    "vwap": extras["vwap"][symbol],
                    "target_weight": weights[symbol],
                    **{f"z_{name}": frame[symbol] for name, frame in per_factor.items()},
                    "z_rvol_5m": confirmation[symbol],
                    **{f"raw_{name}": frame[symbol] for name, frame in factors.items()},
                },
                index=context.index,
            )
            for symbol in symbols
        }
        return StrategySignals(
            target_weights=weights, scores=score, indicators=indicators
        )

    def setup_features(self, signals, context) -> dict[str, pd.DataFrame]:
        """Per-bar quantities worth conditioning trade outcomes on.

        Read off the indicators actually produced rather than from ``FACTORS``:
        an unsigned factor carries no weight and so has no z-score in the sum,
        and naming it here would fail on a config that dropped it.
        """
        published = next(iter(signals.indicators.values())).columns
        names = ["score", "score_rank"] + [c for c in published if c.startswith("z_")]
        return {name: signals.stack(name) for name in names}

    # --------------------------------------------------------------- factors
    def _factors(self, context, symbols):
        panel = context.panel
        close = panel.close[symbols]
        high, low = panel.field("high")[symbols], panel.field("low")[symbols]
        volume = panel.field("volume")[symbols]
        day = pd.Series(session_date(close.index).to_numpy(), index=close.index)
        bar_of_session = day.groupby(day.to_numpy()).cumcount()

        returns = bar_log_returns(close)
        sigma = returns.rolling(self.vol_window, min_periods=self.vol_window // 2).std()
        atr = average_true_range(high, low, close, self.atr_window, restart=day)

        # Session VWAP per symbol; the helper is per-series by design.
        vwap = pd.DataFrame(
            {s: intraday_vwap(panel.bars(s)) for s in symbols}, index=close.index
        )

        # The reference leg of relative_ret_15m. Every tradable symbol is scored
        # against its sector ETF when the universe defines one and the benchmark
        # otherwise, so a sector-wide move is not read as stock-specific.
        universe = context.universe
        reference_of = {
            s: (universe.sectors.get(s) or universe.benchmark) for s in symbols
        }
        full_returns = bar_log_returns(panel.close)
        reference = align_reference(full_returns, reference_of)

        scale = sigma.where(sigma > 0) if self.volatility_normalise else 1.0
        w1, w5, w15, w30 = self.ret_windows
        factors = {
            "ret_1m": trailing_return(returns, w1, restart=day) / scale,
            "ret_5m": trailing_return(returns, w5, restart=day) / scale,
            "ret_15m": trailing_return(returns, w15, restart=day) / scale,
            "ret_30m": trailing_return(returns, w30, restart=day) / scale,
            # Distance from session VWAP in units of the symbol's own bar
            # volatility, so a $500 name and a $30 name are comparable before
            # the cross-sectional step ever runs.
            "vwap_deviation": (close / vwap - 1.0).div(sigma.where(sigma > 0)),
            "relative_ret_15m": (
                trailing_return(returns, self.relative_window, restart=day)
                - trailing_return(reference, self.relative_window, restart=day)
            ),
            "rvol_5m": seasonal_volume_ratio(
                volume, session=day, bar_of_session=bar_of_session,
                window=self.rvol_window,
            ),
            "er_15m": signed_efficiency_ratio(close, self.er_window),
        }
        return factors, {"atr": atr, "vwap": vwap, "sigma": sigma}

    # -------------------------------------------------------------- bar loop
    def _walk(self, context, symbols, close, score, rank, atr, confirmation):
        index = context.index
        n_bars, n_symbols = len(index), len(symbols)
        price = close.to_numpy(dtype=float)
        s = score.reindex(columns=symbols).to_numpy(dtype=float)
        r = rank.reindex(columns=symbols).to_numpy(dtype=float)
        a = atr.reindex(columns=symbols).to_numpy(dtype=float)
        rv = confirmation.reindex(columns=symbols).to_numpy(dtype=float)
        eligible = context.tradable[symbols].to_numpy(dtype=bool)
        printed = context.panel.traded[symbols].to_numpy(dtype=bool)

        opening_blocked = ~self._clock(index, self.no_entry_before, default=True)
        closing_blocked = self._clock(index, self.no_entry_after)
        blocked = opening_blocked | closing_blocked
        flatten = self._clock(index, self.flat_time)
        sessions = session_date(index).to_numpy()

        position = np.zeros(n_symbols, dtype=int)
        entry_price = np.zeros(n_symbols)
        stop = np.full(n_symbols, np.nan)
        held = np.zeros(n_symbols, dtype=int)
        weight = np.zeros(n_symbols)
        out = np.zeros((n_bars, n_symbols))

        size = min(self.max_weight, 1.0 / self.max_positions)

        for i in range(n_bars):
            if i and sessions[i] != sessions[i - 1]:
                # Nothing is carried overnight: the engine has already been told
                # to flatten, and holding state across the gap would let a stop
                # set on yesterday's ATR govern today.
                position[:] = FLAT
                weight[:] = 0.0
                held[:] = 0
                stop[:] = np.nan

            open_now = position != FLAT
            if open_now.any():
                held = np.where(open_now, held + 1, held)
                signed = np.where(position > 0, price[i], -price[i])
                touched = open_now & np.isfinite(stop) & (signed <= stop)
                faded = open_now & (
                    ((position == LONG) & (s[i] < self.exit_long))
                    | ((position == SHORT) & (s[i] > self.exit_short))
                )
                expired = open_now & (held >= self.max_holding_bars)
                closing = (
                    touched | faded | expired | flatten[i] | ~np.isfinite(price[i])
                )
                if closing.any():
                    position = np.where(closing, FLAT, position)
                    weight = np.where(closing, 0.0, weight)
                    held = np.where(closing, 0, held)
                    stop = np.where(closing, np.nan, stop)
            else:
                closing = np.zeros(n_symbols, dtype=bool)

            if not flatten[i] and not blocked[i]:
                healthy = (
                    eligible[i] & printed[i]
                    & np.isfinite(price[i]) & (price[i] > 0)
                    & np.isfinite(s[i]) & np.isfinite(r[i])
                )
                # `~closing`: a symbol that just exited does not reverse on the
                # same bar. It is eligible again from the next one.
                candidate = (position == FLAT) & ~closing & healthy
                if self.min_rvol_z is not None:
                    # Unsigned, so it narrows both sides equally. A NaN reading
                    # is "not measured", which is not confirmation.
                    candidate &= np.isfinite(rv[i]) & (rv[i] >= self.min_rvol_z)
                wants_long = (
                    candidate & self.allow_long
                    & (s[i] > self.entry_long) & (r[i] > self.rank_long)
                )
                wants_short = (
                    candidate & self.allow_short
                    & (s[i] < self.entry_short) & (r[i] < self.rank_short)
                )
                opening = wants_long | wants_short
                room = self.max_positions - int((position != FLAT).sum())
                if opening.any() and room > 0:
                    # More candidates than seats: take the most extreme scores,
                    # which is the same quantity the entry gate tested.
                    order = np.argsort(-np.abs(np.where(opening, s[i], np.nan)))
                    admitted = np.zeros(n_symbols, dtype=bool)
                    admitted[order[:room]] = True
                    admitted &= opening
                    side = np.where(wants_long, LONG, SHORT)
                    position = np.where(admitted, side, position)
                    entry_price = np.where(admitted, price[i], entry_price)
                    weight = np.where(admitted, size * side, weight)
                    held = np.where(admitted, 0, held)
                    if self.stop_atr is not None:
                        distance = self.stop_atr * a[i]
                        signed_entry = np.where(side > 0, price[i], -price[i])
                        stop = np.where(
                            admitted & np.isfinite(distance),
                            signed_entry - distance,
                            np.where(admitted, np.nan, stop),
                        )
            out[i] = weight

        return pd.DataFrame(out, index=index, columns=symbols)

    @staticmethod
    def _clock(index, cutoff: str | None, *, default: bool = False) -> np.ndarray:
        if cutoff is None:
            return np.full(len(index), default, dtype=bool)
        return at_or_after_market_time(
            index, dt.time.fromisoformat(cutoff)
        ).to_numpy()
