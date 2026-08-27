"""Hold a statistically significant trend behind a ratcheting volatility stop.

The rule in one line: **while the drift is significant and the oscillator agrees
with it, be in the trade; let it run behind a stop that only ever moves in your
favour.**

Why a state and not an event
----------------------------
An earlier version of this strategy entered on a MACD crossing that coincided
with a significant trend. That conjunction is the wrong shape for trend
following: a slow, sustained move is significant for most of its length while
the oscillator crosses only at its edges, so the strategy watched entire trends
go past. Measured on real sessions, a stock could grind higher for three hours
with a significant drift and never once present a crossing at a moment when the
trend test also passed.

So the entry is a **state**, asked on every bar. The MACD's job changed with it:
it confirms direction and (optionally) acceleration, and its crossing is used
only as an *exit* signal, where an event is genuinely the right object.

Each of the three decisions is a separate, testable quantity.

1. Is there a trend?
-------------------
Not "is price above a moving average" but "is the drift distinguishable from
what a trendless market would have produced". The drift is standardised against
its sampling distribution **under a driftless random walk**
(:mod:`qtrader.features.trend`), so ``|z| >= trend_z_min`` is a significance
test and ``|z| >= 2`` carries its usual 5% reading. A regression's own
t-statistic is deliberately *not* used: its standard error assumes independent
residuals, and on a random walk it exceeds 2 about 80% of the time.

Every estimator is restricted to the current session, so no overnight gap is
ever read as drift.

Three estimators are selectable, all standardised the same way so a threshold
means the same thing across them:

* ``session`` — drift since the open, ``z = sum(r) / (sigma * sqrt(n))``. Its
  window grows with the day, which is the only way to resolve a slow sustained
  move: a stock that grinds 18% higher over five hours drifts under 2 sigma in
  any single hour, so a fixed span reads noise all day while the session as a
  whole is a 2.3-sigma event. This is the default, because catching such moves
  is what the strategy is for.
* ``ewma`` — exponential weights, fixed memory, defined from the session's
  second bar.
* ``window`` — a fixed OLS window, blind until it is full.

2. When is the turn?
--------------------
MACD is a band-pass filter on price: ``EMA_fast - EMA_slow`` removes the level
and keeps medium-frequency movement, and the histogram ``MACD - signal`` is that
filter's own momentum. A sign change of the histogram (the golden/death cross)
is therefore a change in the sign of medium-frequency acceleration — a timing
event, not a direction forecast. It is used only to time an entry the trend test
has already authorised.

How *hard* the oscillator is moving is separately measurable. The histogram's
one-bar change is a linear filter of returns, so it can be standardised against
a driftless random walk exactly as the drift is
(:func:`qtrader.features.momentum.macd_cross_zscore`). ``min_cross_zscore`` then
means "only enter while the lines are still diverging in my favour, by more than
noise would manage". It is off by default: it looks directionally helpful
in-sample but does not improve the per-trade t-statistic, so there is no
evidence for a particular level.

The MACD itself is restarted at each session, so no overnight gap can leak into
it — a gap would otherwise appear as a spurious spike, the fast EMA absorbing it
before the slow one.

3. How much, and when to get out?
---------------------------------
Size by risk, not by conviction. With ``sigma_H = sigma_bar * sqrt(horizon)``
the relative price volatility over the intended holding horizon (diffusion
scaling), the initial stop sits ``s`` of those sigmas away, so a position of
weight ``w`` loses ``w * s * sigma_H`` of capital if stopped::

    w = clip( risk_per_trade / (s * sigma_H),  0,  max_weight )

Higher-volatility names therefore get smaller positions for the same risk. The
weight is frozen at entry — re-deriving it every bar would emit an order every
bar without changing the position materially.

The exit is a single monotone barrier. For a long, with ``M_t`` the highest
close since entry::

    stop_t = max( entry - s * sigma_H * P,  M_t - r * sigma_H * P )     r >= s

* it starts at ``entry - s*sigma_H`` — an immediate loss is capped at one risk
  unit;
* it never falls, because ``M_t`` never falls, so profit already banked cannot
  be given back past the ratchet;
* it only starts trailing once ``M_t - entry > (r - s) * sigma_H * P``, i.e.
  once the trade is genuinely ahead. Before that the initial stop governs.

That is exactly "cut the loss immediately, let the profit run, and take it if
the retracement exceeds a set range" — with the range set by the volatility the
prior bars actually displayed rather than by a fixed percentage.

Getting back out, and back in
-----------------------------
Three things release a position: the barrier, the clock, and the trend statistic
changing sides — ``z`` reaching ``trend_z_reset`` on the *other* side, meaning
the case for the trade has lapsed rather than merely paused. The opposite MACD
crossing is available as a fourth (``exit_on_opposite_cross``) but is off by
default, because a sustained trend flips the histogram repeatedly and exiting on
each flip caps holding periods at a handful of bars.

``trend_z_reset`` does double duty as hysteresis on the way back in: after an
exit a symbol is *disarmed* until ``|z|`` has cooled below that level. Without
it, a stop taken inside a still-significant trend would be followed by an
immediate re-entry, and the book would grind against the same move all session.

Where the model is conservative
-------------------------------
Barriers are evaluated on **closing prices**, not intrabar highs and lows, and
the exit is filled at the next bar's open like every other signal. A backtest
that stops out at the exact barrier price inside the bar is claiming a fill the
simulator cannot produce. The cost of honesty is that a gap through the stop
loses more than one risk unit — which is also what happens in reality.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..data.sessions import at_or_after_market_time, session_date
from ..features.momentum import macd_cross_zscore, session_macd
from ..features.relative import bar_log_returns
from ..features.seasonality import seasonal_volatility
from ..features.trend import drift_zscore, ewma_drift_zscore, session_drift_zscore
from .base import MarketContext, Strategy, StrategySignals

FLAT, LONG, SHORT = 0, 1, -1

#: How the trailing drift is estimated. See :meth:`_trend_zscore`.
TREND_ESTIMATORS = ("session", "ewma", "window")


class TrendRatchetStrategy(Strategy):
    name = "trend_ratchet"

    def __init__(
        self,
        *,
        # --- momentum trigger
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # --- trend test
        trend_estimator: str = "session",
        trend_span: int = 60,
        trend_window: int = 60,
        trend_z_min: float = 2.0,
        trend_z_reset: float = 1.0,
        session_confirm_z: float | None = None,
        min_cross_zscore: float = 0.0,
        # --- risk geometry
        vol_window: int = 120,
        seasonal_volatility_profile: bool = True,
        horizon_bars: int = 60,
        stop_sigmas: float = 1.0,
        trail_sigmas: float = 1.5,
        # --- sizing and book limits
        risk_per_trade: float = 0.001,
        max_weight: float = 0.15,
        max_positions: int = 6,
        allow_short: bool = True,
        # --- session discipline
        exit_on_opposite_cross: bool = False,
        no_entry_after: str | None = "15:00",
        flat_time: str | None = "15:50",
    ):
        if macd_fast >= macd_slow:
            raise ValueError(f"macd_fast ({macd_fast}) must be shorter than macd_slow ({macd_slow})")
        if trail_sigmas < stop_sigmas:
            raise ValueError(
                f"trail_sigmas ({trail_sigmas}) must be at least stop_sigmas ({stop_sigmas}); "
                "a trailing stop tighter than the initial one would make the initial stop "
                "unreachable and the risk unit meaningless"
            )
        if max_positions * max_weight > 1.0 + 1e-9:
            raise ValueError(
                f"max_positions ({max_positions}) x max_weight ({max_weight}) exceeds the "
                "gross budget of 1; the book could not be held at the sizes it asks for"
            )
        if trend_z_min <= 0:
            raise ValueError("trend_z_min must be positive; it is a significance threshold")
        if trend_estimator not in TREND_ESTIMATORS:
            raise ValueError(
                f"trend_estimator must be one of {TREND_ESTIMATORS}, got {trend_estimator!r}"
            )
        if min_cross_zscore < 0:
            raise ValueError("min_cross_zscore is a magnitude threshold and cannot be negative")
        if session_confirm_z is not None and session_confirm_z < 0:
            raise ValueError("session_confirm_z is a magnitude threshold and cannot be negative")
        if not 0.0 <= trend_z_reset < trend_z_min:
            raise ValueError(
                f"trend_z_reset ({trend_z_reset}) must be in [0, trend_z_min={trend_z_min}); "
                "without a gap between them the book would re-enter on the bar after every stop"
            )

        self.macd_fast = int(macd_fast)
        self.macd_slow = int(macd_slow)
        self.macd_signal = int(macd_signal)
        self.trend_estimator = trend_estimator
        self.trend_span = int(trend_span)
        self.trend_window = int(trend_window)
        self.trend_z_min = float(trend_z_min)
        self.trend_z_reset = float(trend_z_reset)
        self.session_confirm_z = (
            None if session_confirm_z is None else float(session_confirm_z)
        )
        self.min_cross_zscore = float(min_cross_zscore)
        self.vol_window = int(vol_window)
        self.seasonal_volatility_profile = bool(seasonal_volatility_profile)
        self.horizon_bars = int(horizon_bars)
        self.stop_sigmas = float(stop_sigmas)
        self.trail_sigmas = float(trail_sigmas)
        self.risk_per_trade = float(risk_per_trade)
        self.max_weight = float(max_weight)
        self.max_positions = int(max_positions)
        self.allow_short = bool(allow_short)
        self.exit_on_opposite_cross = bool(exit_on_opposite_cross)
        self.no_entry_after = no_entry_after
        self.flat_time = flat_time

    # ---------------------------------------------------------------- signal
    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        close = context.panel.close[symbols]

        day, bar_of_session = self._session_index(close.index)
        macd_panel = session_macd(
            close,
            session=day,
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal,
        )
        histogram = macd_panel["macd_hist"]
        per_bar_vol = self._per_bar_volatility(close, day, bar_of_session)
        trend_z = self._trend_zscore(close, per_bar_vol, day, bar_of_session)
        session_z = self._session_confirmation(close, per_bar_vol, day, trend_z)
        cross_z = macd_cross_zscore(
            histogram,
            close,
            volatility=per_bar_vol,
            bar_of_session=bar_of_session,
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal,
        )
        sigma = per_bar_vol * np.sqrt(self.horizon_bars)

        weights = self._walk(context, close, histogram, trend_z, session_z, cross_z, sigma)
        indicators = {
            symbol: pd.DataFrame(
                {
                    "macd": macd_panel["macd"][symbol],
                    "macd_signal": macd_panel["macd_signal"][symbol],
                    "macd_hist": histogram[symbol],
                    "trend_zscore": trend_z[symbol],
                    "session_zscore": session_z[symbol],
                    "cross_zscore": cross_z[symbol],
                    "horizon_sigma_bps": sigma[symbol] * 1e4,
                    "target_weight": weights[symbol],
                },
                index=context.index,
            )
            for symbol in symbols
        }
        return StrategySignals(
            target_weights=weights, scores=trend_z[symbols], indicators=indicators
        )

    def setup_features(self, signals, context) -> dict[str, pd.DataFrame]:
        """What the rule looked at, in units comparable across symbols."""
        trend = signals.stack("trend_zscore")
        cross = signals.stack("cross_zscore")
        return {
            "trend_zscore": trend,
            "abs_trend_zscore": trend.abs(),
            "cross_zscore": cross,
            "abs_cross_zscore": cross.abs(),
            "horizon_sigma_bps": signals.stack("horizon_sigma_bps"),
            "macd_hist_bps": signals.stack("macd_hist")
            / context.panel.close[list(context.symbols)]
            * 1e4,
        }

    # ------------------------------------------------------------- ingredients
    @staticmethod
    def _session_index(index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
        """Session date per bar, and how many bars into the session each one is."""
        day = session_date(index)
        return day, day.groupby(day.to_numpy()).cumcount()

    def _per_bar_volatility(
        self, close: pd.DataFrame, day: pd.Series, bar_of_session: pd.Series
    ) -> pd.DataFrame:
        """Per-bar standard deviation of intraday log returns.

        One volatility estimate serves both jobs: it is the scale the trend is
        judged against and the unit the stop distance is measured in, so "a
        2-sigma trend" and "a 1-sigma stop" refer to the same sigma. Overnight
        gaps are excluded — they are news, not intraday diffusion.

        With ``seasonal_volatility_profile`` the estimate carries the intraday
        U-shape (:mod:`qtrader.features.seasonality`). Without it, a flat sigma
        understates the open by three to four times, and every threshold built
        on sigma is correspondingly too loose exactly when the market is
        wildest.
        """
        returns = bar_log_returns(close, within_session=True)
        if not self.seasonal_volatility_profile:
            return returns.rolling(self.vol_window, min_periods=self.vol_window // 2).std()
        return seasonal_volatility(
            returns,
            session=day,
            bar_of_session=bar_of_session,
            window=self.vol_window,
            min_periods=self.vol_window // 2,
        )

    def _session_confirmation(
        self,
        close: pd.DataFrame,
        per_bar_vol: pd.DataFrame,
        day: pd.Series,
        trend_z: pd.DataFrame,
    ) -> pd.DataFrame:
        """The day's drift, used to confirm what a faster trigger has spotted.

        Two timescales answer two different questions. The trigger asks "is it
        moving *now*"; this asks "is today a trending day at all, and in which
        direction". Gating the fast signal on the slow one is what lets an entry
        happen early in a move without abandoning the requirement that the move
        be real — the alternative, waiting for the slow statistic to clear its
        own threshold, is late by construction, because cumulative evidence
        reaches significance only once most of the move has happened.

        Returns the trigger itself when confirmation is disabled, so the caller
        has one code path.
        """
        if self.session_confirm_z is None:
            return trend_z
        returns = bar_log_returns(close, within_session=True)
        return session_drift_zscore(returns, volatility=per_bar_vol, restart=day)

    def _trend_zscore(
        self,
        close: pd.DataFrame,
        per_bar_vol: pd.DataFrame,
        day: pd.Series,
        bar_of_session: pd.Series,
    ) -> pd.DataFrame:
        """Standardised drift, by whichever estimator the config selected.

        ``ewma`` is defined from a session's second bar, because its standard
        error accounts for how few returns it has seen; ``window`` says nothing
        until ``trend_window`` same-session bars exist, which costs the first
        hour of every session but weights those bars equally.

        Both are standardised by the same per-bar sigma that scales the risk
        geometry below, so "a 2.5-sigma trend" and "a 1-sigma stop" refer to one
        quantity whichever estimator is in use.
        """
        if self.trend_estimator == "session":
            returns = bar_log_returns(close, within_session=True)
            return session_drift_zscore(returns, volatility=per_bar_vol, restart=day)

        if self.trend_estimator == "ewma":
            returns = bar_log_returns(close, within_session=True)
            return ewma_drift_zscore(
                returns, self.trend_span, volatility=per_bar_vol, restart=day
            )

        z = drift_zscore(np.log(close), self.trend_window, volatility=per_bar_vol)
        z.loc[(bar_of_session < self.trend_window - 1).to_numpy()] = np.nan
        return z

    # ------------------------------------------------------------- state walk
    def _walk(
        self,
        context: MarketContext,
        close: pd.DataFrame,
        histogram: pd.DataFrame,
        trend_z: pd.DataFrame,
        session_z: pd.DataFrame,
        cross_z: pd.DataFrame,
        sigma: pd.DataFrame,
    ) -> pd.DataFrame:
        """Bar-by-bar position state.

        The barrier is path-dependent — the stop depends on the best price seen
        since entry — so this cannot be expressed as a vectorised transform of
        the inputs. The loop runs over bars and is vectorised across symbols.
        """
        symbols = list(context.symbols)
        n_bars, n_symbols = len(context.index), len(symbols)

        price = close.to_numpy(dtype=float)
        hist = histogram.to_numpy(dtype=float)
        zscore = trend_z.to_numpy(dtype=float)
        confirm = session_z.to_numpy(dtype=float)
        crossing = cross_z.to_numpy(dtype=float)
        vol = sigma.to_numpy(dtype=float)
        tradable = context.tradable[symbols].to_numpy(dtype=bool)

        crossed_up, crossed_down = _sign_changes(hist)
        blocked = self._blocked_entries(context.index)
        flatten = self._flatten_bars(context.index)

        position = np.zeros(n_symbols, dtype=int)
        # A symbol is armed once |z| has cooled below the reset level. Everything
        # starts armed; an exit disarms until the trend has genuinely lapsed.
        armed = np.ones(n_symbols, dtype=bool)
        entry_price = np.zeros(n_symbols)
        extreme = np.zeros(n_symbols)
        risk_unit = np.zeros(n_symbols)
        trail_unit = np.zeros(n_symbols)
        weight = np.zeros(n_symbols)
        out = np.zeros((n_bars, n_symbols))

        for i in range(n_bars):
            quiet = np.isfinite(zscore[i]) & (np.abs(zscore[i]) < self.trend_z_reset)
            armed |= quiet

            open_now = position != 0
            if open_now.any():
                # Work in "favourable" coordinates: +price for a long, -price for
                # a short, so one set of comparisons covers both directions.
                favourable = np.where(position > 0, price[i], -price[i])
                signed_entry = np.where(position > 0, entry_price, -entry_price)
                # The ratchet: the extreme only ever moves in the trade's favour.
                extreme = np.where(open_now, np.maximum(extreme, favourable), extreme)
                stop = np.maximum(signed_entry - risk_unit, extreme - trail_unit)

                breached = open_now & (favourable <= stop)
                lapsed = open_now & self._trend_turned(position, zscore[i])
                flipped = open_now & self._opposite_cross(position, crossed_up[i], crossed_down[i])
                closing = breached | lapsed | flipped | flatten[i] | ~np.isfinite(price[i])

                position = np.where(closing, FLAT, position)
                weight = np.where(closing, 0.0, weight)
                armed = np.where(closing, False, armed)

            if not flatten[i] and not blocked[i]:
                direction = self._entry_direction(
                    hist[i], zscore[i], confirm[i], crossing[i], tradable[i], position, armed
                )
                opening = direction != FLAT
                if opening.any():
                    opening = self._respect_book_limit(opening, position, zscore[i])
                if opening.any():
                    size = self._entry_weight(vol[i])
                    position = np.where(opening, direction, position)
                    entry_price = np.where(opening, price[i], entry_price)
                    extreme = np.where(
                        opening, np.where(direction > 0, price[i], -price[i]), extreme
                    )
                    risk_unit = np.where(opening, self.stop_sigmas * vol[i] * price[i], risk_unit)
                    trail_unit = np.where(opening, self.trail_sigmas * vol[i] * price[i], trail_unit)
                    weight = np.where(opening, size, weight)
                    armed = np.where(opening, False, armed)

            out[i] = position * weight

        return pd.DataFrame(out, index=context.index, columns=symbols)

    # ------------------------------------------------------------- decisions
    def _entry_direction(
        self,
        histogram: np.ndarray,
        zscore: np.ndarray,
        confirm: np.ndarray,
        crossing: np.ndarray,
        tradable: np.ndarray,
        position: np.ndarray,
        armed: np.ndarray,
    ) -> np.ndarray:
        """Direction to open per symbol, or FLAT where no entry is authorised.

        A **state**, evaluated every bar, not an event. The earlier design fired
        only on a MACD crossing, which meant a trend had to produce a crossing
        while it was also significant — and a slow, sustained move often does
        neither at the same moment, so the strategy watched entire trends go by.
        Here the conditions are simply asked on every bar:

        * the drift is significant (``|z| >= trend_z_min``);
        * the oscillator agrees with it (histogram on the same side as ``z``),
          which is what keeps the entry from fighting short-term momentum;
        * optionally, the histogram is still opening in the trade's favour by
          ``min_cross_zscore`` standard deviations — the calibrated form of "the
          lines are diverging, not converging";
        * the symbol is ``armed``: since its last exit, ``|z|`` has fallen back
          under ``trend_z_reset``. Without that hysteresis a stop-out inside a
          live trend would be followed by an immediate re-entry, and the book
          would grind against the same move all session.
        """
        significant = np.isfinite(zscore) & (np.abs(zscore) >= self.trend_z_min)
        confirmed_long = (zscore > 0) & (histogram > 0)
        confirmed_short = (zscore < 0) & (histogram < 0)

        if self.session_confirm_z is not None:
            level = self.session_confirm_z
            confirmed_long &= np.isfinite(confirm) & (confirm >= level)
            confirmed_short &= np.isfinite(confirm) & (confirm <= -level)

        accelerating_long = np.isfinite(crossing) & (crossing >= self.min_cross_zscore)
        accelerating_short = np.isfinite(crossing) & (crossing <= -self.min_cross_zscore)

        eligible = (position == FLAT) & tradable & significant & armed
        direction = np.where(eligible & confirmed_long & accelerating_long, LONG, FLAT)
        if self.allow_short:
            direction = np.where(
                eligible & confirmed_short & accelerating_short, SHORT, direction
            )
        return direction

    def _respect_book_limit(
        self, opening: np.ndarray, position: np.ndarray, zscore: np.ndarray
    ) -> np.ndarray:
        """Keep at most ``max_positions``, preferring the strongest evidence.

        When more entries qualify than there are slots, the tie is broken by
        |z| — the candidates whose trends are least likely to be noise.
        """
        free = self.max_positions - int((position != FLAT).sum())
        candidates = np.flatnonzero(opening)
        if free <= 0:
            return np.zeros_like(opening)
        if len(candidates) <= free:
            return opening

        strongest = candidates[np.argsort(-np.abs(zscore[candidates]))[:free]]
        limited = np.zeros_like(opening)
        limited[strongest] = True
        return limited

    def _entry_weight(self, sigma: np.ndarray) -> np.ndarray:
        """Fixed-fractional risk sizing, capped."""
        stop_distance = self.stop_sigmas * sigma
        with np.errstate(divide="ignore", invalid="ignore"):
            size = np.where(stop_distance > 0, self.risk_per_trade / stop_distance, 0.0)
        return np.clip(np.nan_to_num(size), 0.0, self.max_weight)

    def _trend_turned(self, position: np.ndarray, zscore: np.ndarray) -> np.ndarray:
        """The evidence has changed sides: ``z`` now argues against the position.

        This is the exit that belongs with a state entry. A histogram flip is far
        too frequent — a sustained trend produces many of them — so exiting on
        every one caps holding periods at a few bars and defeats the ratchet.
        Requiring the *drift statistic* to reach ``trend_z_reset`` on the other
        side is the same hysteresis band the re-entry rule uses, read from the
        other direction: the position is held until the case for it has lapsed,
        not merely paused.
        """
        against = np.where(position > 0, -zscore, zscore)
        return np.isfinite(zscore) & (against >= self.trend_z_reset)

    def _opposite_cross(
        self, position: np.ndarray, crossed_up: np.ndarray, crossed_down: np.ndarray
    ) -> np.ndarray:
        if not self.exit_on_opposite_cross:
            return np.zeros_like(crossed_up)
        return ((position > 0) & crossed_down) | ((position < 0) & crossed_up)

    # ---------------------------------------------------------------- clocks
    def _blocked_entries(self, index: pd.DatetimeIndex) -> np.ndarray:
        """Bars too late in the session to give a new position its horizon."""
        if self.no_entry_after is None:
            return np.zeros(len(index), dtype=bool)
        cutoff = dt.time.fromisoformat(self.no_entry_after)
        return at_or_after_market_time(index, cutoff).to_numpy()

    def _flatten_bars(self, index: pd.DatetimeIndex) -> np.ndarray:
        if self.flat_time is None:
            return np.zeros(len(index), dtype=bool)
        cutoff = dt.time.fromisoformat(self.flat_time)
        return at_or_after_market_time(index, cutoff).to_numpy()


def _sign_changes(histogram: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Golden and death crosses: bars where the histogram changes sign.

    A cross is defined on the *previous* bar's sign, so it is knowable at the
    close of the bar it is reported on.
    """
    previous = np.vstack([np.full((1, histogram.shape[1]), np.nan), histogram[:-1]])
    up = (histogram > 0) & (previous <= 0) & np.isfinite(previous)
    down = (histogram < 0) & (previous >= 0) & np.isfinite(previous)
    return up, down
