"""Time-series momentum score for a broad-index ETF, one decision per minute.

A cross-sectional z-score is undefined on one name (its sample std is NaN) and
is the wrong object even on two: it removes the market-common move, which *is*
the index. Every factor here is computed from that symbol's own path, in the
same random-walk units the rest of the project uses, then averaged.

The six directional terms
-------------------------
``session_z``
    Drift since the open, ``sum(r)/(sigma*sqrt(n))``. The only estimator whose
    window grows with the day.
``ewma_z``
    Exponentially weighted drift, session-restarted. Can reverse after a large
    open; ``session_z`` cannot.
``ret_5_z`` / ``ret_15_z``
    Trailing log return over 5 / 15 in-session bars, divided by ``sigma*sqrt(n)``.
    Short and medium impulses, same units as the drift z-scores.
``vwap_z``
    ``(close/session VWAP - 1) / (sigma * sqrt(n))``. Location versus the day's
    own volume centre, in the same random-walk units as ``session_z``.
``macd_z``
    Session-restarted MACD histogram velocity in random-walk sigmas.

Unsigned confirmation, not a vote: ``rvol`` (volume vs the same minute of prior
sessions). ``er_15`` is signed but on ``[-1, 1]``, so it is published and can
gate on ``|er|``; it is not averaged with the z-scores.

Known prior: R04/R05 measured *stock* intraday momentum as wrong-signed. This
score is a different claim — one index's own drift — and must be judged on its
own ladder, not inherited from that result.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import numpy as np
import pandas as pd

from ..data.sessions import at_or_after_market_time, session_date
from ..features.efficiency import signed_efficiency_ratio
from ..features.levels import average_true_range
from ..features.momentum import macd_cross_zscore, session_macd
from ..features.relative import bar_log_returns, trailing_return
from ..features.score import combine_score
from ..features.seasonality import seasonal_volatility, seasonal_volume_ratio
from ..features.stock import intraday_vwap
from ..features.trend import ewma_drift_zscore, session_drift_zscore
from .base import MarketContext, Strategy, StrategySignals

FLAT, LONG, SHORT = 0, 1, -1

#: Directional factors, all in random-walk (or bar-sigma) units.
FACTORS = (
    "session_z", "ewma_z", "ret_5_z", "ret_15_z", "vwap_z", "macd_z",
)

#: Present on the indicator frame, never in the weighted sum. ``er_15`` is
#: signed but lives on [-1, 1], which is not a random-walk sigma.
NOT_SCORED = frozenset({"rvol", "er_15"})

DEFAULT_WEIGHTS: dict[str, float] = {name: 1.0 for name in FACTORS}


class IndexMomentumStrategy(Strategy):
    """Own-path intraday score for index ETFs. No cross-section."""

    name = "index_momentum"

    def __init__(
        self,
        *,
        ret_windows: tuple[int, int] = (5, 15),
        ewma_span: int = 12,
        er_window: int = 15,
        rvol_window: int = 5,
        vol_window: int = 30,
        atr_window: int = 30,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        weights: Mapping[str, float] | None = None,
        min_factors: int = 4,
        min_rvol: float | None = 1.0,
        min_er: float | None = None,
        entry_long: float = 1.5,
        entry_short: float = -1.5,
        exit_long: float = 0.0,
        exit_short: float = 0.0,
        max_holding_bars: int | None = None,
        min_holding_bars: int = 10,
        reentry_cooldown_bars: int = 15,
        stop_atr: float | None = 2.0,
        max_positions: int = 2,
        max_weight: float = 0.5,
        allow_long: bool = True,
        allow_short: bool = True,
        no_entry_before: str | None = "09:40",
        no_entry_after: str | None = "15:30",
        flat_time: str | None = "15:50",
    ):
        if not allow_long and not allow_short:
            raise ValueError("at least one of allow_long / allow_short must be enabled")
        if entry_long <= exit_long:
            raise ValueError("entry_long must be above exit_long")
        if entry_short >= exit_short:
            raise ValueError("entry_short must be below exit_short")
        if max_holding_bars is not None and max_holding_bars < 1:
            raise ValueError("max_holding_bars must be at least 1")
        if min_holding_bars < 0:
            raise ValueError("min_holding_bars cannot be negative")
        if reentry_cooldown_bars < 0:
            raise ValueError("reentry_cooldown_bars cannot be negative")
        if stop_atr is not None and stop_atr <= 0:
            raise ValueError("stop_atr must be positive")
        if max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if not 0.0 < max_weight <= 1.0:
            raise ValueError("max_weight must lie in (0, 1]")
        if min_factors < 1:
            raise ValueError("min_factors must be at least 1")
        if macd_fast >= macd_slow:
            raise ValueError("macd_fast must be shorter than macd_slow")

        self.ret_windows = tuple(int(w) for w in ret_windows)
        if len(self.ret_windows) != 2 or any(w < 1 for w in self.ret_windows):
            raise ValueError("ret_windows must be two positive bar counts")
        self.ewma_span = int(ewma_span)
        self.er_window = int(er_window)
        self.rvol_window = int(rvol_window)
        self.vol_window = int(vol_window)
        self.atr_window = int(atr_window)
        self.macd_fast = int(macd_fast)
        self.macd_slow = int(macd_slow)
        self.macd_signal = int(macd_signal)
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        not_scored = set(self.weights) & NOT_SCORED
        if not_scored:
            raise ValueError(
                f"{sorted(not_scored)} are confirmation gates, not scored factors"
            )
        unknown = set(self.weights) - set(FACTORS)
        if unknown:
            raise ValueError(f"unknown factors in weights: {sorted(unknown)}")
        self.min_factors = int(min_factors)
        self.min_rvol = None if min_rvol is None else float(min_rvol)
        self.min_er = None if min_er is None else float(min_er)

        self.entry_long = float(entry_long)
        self.entry_short = float(entry_short)
        self.exit_long = float(exit_long)
        self.exit_short = float(exit_short)
        self.max_holding_bars = (
            None if max_holding_bars is None else int(max_holding_bars)
        )
        self.min_holding_bars = int(min_holding_bars)
        self.reentry_cooldown_bars = int(reentry_cooldown_bars)
        self.stop_atr = None if stop_atr is None else float(stop_atr)
        self.max_positions = int(max_positions)
        self.max_weight = float(max_weight)
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        self.no_entry_before = no_entry_before
        self.no_entry_after = no_entry_after
        self.flat_time = flat_time

    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        close = context.panel.close[symbols]
        factors, extras = self._factors(context, symbols)
        score = combine_score(factors, self.weights, min_factors=self.min_factors)
        weights = self._walk(
            context, symbols, close, score, extras["atr"], extras["rvol"], extras["er"]
        )
        indicators = {
            symbol: pd.DataFrame(
                {
                    "score": score[symbol],
                    "atr": extras["atr"][symbol],
                    "vwap": extras["vwap"][symbol],
                    "rvol": extras["rvol"][symbol],
                    "er_15": extras["er"][symbol],
                    "target_weight": weights[symbol],
                    "macd": extras["macd"][symbol],
                    "macd_signal": extras["macd_signal"][symbol],
                    "macd_hist": extras["macd_hist"][symbol],
                    **{name: frame[symbol] for name, frame in factors.items()},
                },
                index=context.index,
            )
            for symbol in symbols
        }
        return StrategySignals(
            target_weights=weights, scores=score, indicators=indicators
        )

    def setup_features(self, signals, context) -> dict[str, pd.DataFrame]:
        published = next(iter(signals.indicators.values())).columns
        names = ["score"] + [c for c in published if c in FACTORS or c in ("rvol", "er_15")]
        return {name: signals.stack(name) for name in names}

    def _factors(self, context, symbols):
        panel = context.panel
        close = panel.close[symbols]
        high, low = panel.field("high")[symbols], panel.field("low")[symbols]
        volume = panel.field("volume")[symbols]
        day = session_date(close.index)
        bar_of_session = day.groupby(day.to_numpy()).cumcount()

        returns = bar_log_returns(close)
        # Profile each name from its own history. Pooling would let QQQ's
        # open-shape leak into SPY's z-scores, which is the cross-section this
        # strategy exists to avoid.
        sigma = pd.concat(
            {
                symbol: seasonal_volatility(
                    returns[[symbol]],
                    session=day,
                    bar_of_session=bar_of_session,
                    window=self.vol_window,
                )[symbol]
                for symbol in symbols
            },
            axis=1,
        )
        atr = average_true_range(high, low, close, self.atr_window, restart=day)
        vwap = pd.DataFrame(
            {s: intraday_vwap(panel.bars(s)) for s in symbols}, index=close.index
        )
        macd_panel = session_macd(
            close, session=day,
            fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal,
        )
        w5, w15 = self.ret_windows
        scale5 = sigma * np.sqrt(w5)
        scale15 = sigma * np.sqrt(w15)
        elapsed = bar_of_session.astype(float) + 1.0
        session_scale = sigma.mul(np.sqrt(elapsed), axis=0)
        factors = {
            "session_z": session_drift_zscore(returns, volatility=sigma, restart=day),
            "ewma_z": ewma_drift_zscore(
                returns, self.ewma_span, volatility=sigma, restart=day
            ),
            "ret_5_z": trailing_return(returns, w5, restart=day) / scale5.where(scale5 > 0),
            "ret_15_z": trailing_return(returns, w15, restart=day) / scale15.where(scale15 > 0),
            # Divide by sigma*sqrt(n), not sigma: (close/VWAP-1) is a session-scale
            # return, and one-bar sigma would let this term dominate the average.
            "vwap_z": (close / vwap - 1.0).div(session_scale.where(session_scale > 0)),
            "macd_z": macd_cross_zscore(
                macd_panel["macd_hist"], close, volatility=sigma,
                bar_of_session=bar_of_session,
                fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal,
            ),
        }
        extras = {
            "atr": atr,
            "vwap": vwap,
            "sigma": sigma,
            "rvol": seasonal_volume_ratio(
                volume, session=day, bar_of_session=bar_of_session, window=self.rvol_window,
            ),
            "er": signed_efficiency_ratio(close, self.er_window),
            "macd": macd_panel["macd"],
            "macd_signal": macd_panel["macd_signal"],
            "macd_hist": macd_panel["macd_hist"],
        }
        return factors, extras

    def _walk(self, context, symbols, close, score, atr, rvol, er):
        index = context.index
        n_bars, n_symbols = len(index), len(symbols)
        price = close.to_numpy(dtype=float)
        s = score.reindex(columns=symbols).to_numpy(dtype=float)
        a = atr.reindex(columns=symbols).to_numpy(dtype=float)
        rv = rvol.reindex(columns=symbols).to_numpy(dtype=float)
        efficiency = er.reindex(columns=symbols).to_numpy(dtype=float)
        eligible = context.tradable[symbols].to_numpy(dtype=bool)
        printed = context.panel.traded[symbols].to_numpy(dtype=bool)

        opening_blocked = ~self._clock(index, self.no_entry_before, default=True)
        closing_blocked = self._clock(index, self.no_entry_after)
        blocked = opening_blocked | closing_blocked
        flatten = self._clock(index, self.flat_time)
        sessions = session_date(index).to_numpy()

        position = np.zeros(n_symbols, dtype=int)
        stop = np.full(n_symbols, np.nan)
        held = np.zeros(n_symbols, dtype=int)
        cooldown = np.zeros(n_symbols, dtype=int)
        weight = np.zeros(n_symbols)
        out = np.zeros((n_bars, n_symbols))
        size = min(self.max_weight, 1.0 / self.max_positions)

        for i in range(n_bars):
            if i and sessions[i] != sessions[i - 1]:
                position[:] = FLAT
                weight[:] = 0.0
                held[:] = 0
                stop[:] = np.nan
                cooldown[:] = 0

            cooldown = np.where(
                position == FLAT, np.maximum(cooldown - 1, 0), cooldown
            )

            open_now = position != FLAT
            if open_now.any():
                held = np.where(open_now, held + 1, held)
                signed = np.where(position > 0, price[i], -price[i])
                touched = open_now & np.isfinite(stop) & (signed <= stop)
                faded = (
                    open_now
                    & (held >= self.min_holding_bars)
                    & (
                        ((position == LONG) & (s[i] < self.exit_long))
                        | ((position == SHORT) & (s[i] > self.exit_short))
                    )
                )
                expired = (
                    open_now & (held >= self.max_holding_bars)
                    if self.max_holding_bars is not None
                    else np.zeros(n_symbols, dtype=bool)
                )
                closing = (
                    touched | faded | expired | flatten[i] | ~np.isfinite(price[i])
                )
                if closing.any():
                    position = np.where(closing, FLAT, position)
                    weight = np.where(closing, 0.0, weight)
                    held = np.where(closing, 0, held)
                    stop = np.where(closing, np.nan, stop)
                    cooldown = np.where(
                        closing, self.reentry_cooldown_bars, cooldown
                    )
            else:
                closing = np.zeros(n_symbols, dtype=bool)

            if not flatten[i] and not blocked[i]:
                healthy = (
                    eligible[i] & printed[i]
                    & np.isfinite(price[i]) & (price[i] > 0)
                    & np.isfinite(s[i])
                )
                candidate = (
                    (position == FLAT) & ~closing & healthy & (cooldown <= 0)
                )
                if self.min_rvol is not None:
                    candidate &= np.isfinite(rv[i]) & (rv[i] >= self.min_rvol)
                if self.min_er is not None:
                    candidate &= np.isfinite(efficiency[i]) & (
                        np.abs(efficiency[i]) >= self.min_er
                    )
                wants_long = (
                    candidate & self.allow_long & (s[i] > self.entry_long)
                )
                wants_short = (
                    candidate & self.allow_short & (s[i] < self.entry_short)
                )
                opening = wants_long | wants_short
                room = self.max_positions - int((position != FLAT).sum())
                if opening.any() and room > 0:
                    order = np.argsort(-np.abs(np.where(opening, s[i], np.nan)))
                    admitted = np.zeros(n_symbols, dtype=bool)
                    admitted[order[:room]] = True
                    admitted &= opening
                    side = np.where(wants_long, LONG, SHORT)
                    position = np.where(admitted, side, position)
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
