"""Break-and-retest of ex-ante S/R levels, confirmed by momentum.

Implements the continuation sleeve of `docs/research/deep-research-report.md`:
a level is broken, the break is retested and holds, momentum agrees, and the
position is then released by the same volatility barrier `trend_ratchet` uses.

    break        close beyond L by more than the level zone
    retest       price returns into [L - d, L + d] within retest_bars
    hold         and closes back on the breakout side
    momentum     drift z-score agrees with the breakout direction
    participation relative volume at the break clears rvol_min
    context      price on the breakout side of the session VWAP

An optional **confirmation** frame vetoes entries. It is supplied from outside
rather than computed here so that a heavy external model can be run once over
the candidate signals and cached, and so that the same candidates can be
backtested with and without it. See `scripts/kronos_confirm.py`.

What is already known about this
--------------------------------
R07 measured both ex-ante level families this strategy triggers on and found
nothing: round-number crossings continue at −0.0035 sigma against −0.0030 for
placebo offsets over 170,038 events, and previous-day extremes sit inside their
own placebo range. R04/R05 measured intraday momentum as reliably wrong-signed
on this universe (rank IC −0.023, t = −11.2).

The strategy is implemented faithfully anyway, because the request was to let
the test decide. Those measurements are the prior it is being tested against.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..data.sessions import at_or_after_market_time, session_date
from ..features.levels import (
    average_true_range,
    level_zone,
    nearest_round_levels,
    opening_range_levels,
    previous_day_levels,
)
from ..features.relative import bar_log_returns
from ..features.seasonality import seasonal_volatility
from ..features.trend import ewma_drift_zscore, session_drift_zscore

#: How the momentum filter measures drift. ``session`` accumulates every return
#: since the open and so cannot reverse within a session once a large early move
#: has happened; ``ewma`` weights recent bars more heavily and can.
MOMENTUM_ESTIMATORS = ("session", "ewma")

#: How much capital an entry gets. ``risk`` makes `weight x stop_distance` a
#: fixed fraction of equity, so a tight stop earns a large position; ``equal``
#: gives every entry `max_weight`. The choice matters more than it looks: `risk`
#: sizing is anti-correlated with volatility by construction, and on this
#: universe the edge is *larger* in volatile names, so it bets most on the
#: setups with least signal (R10 §5h).
SIZING_RULES = ("risk", "equal")
from ..models.kronos_confirm import load_scores
from .base import MarketContext, Strategy, StrategySignals

FLAT, LONG, SHORT = 0, 1, -1


class SRMomentumStrategy(Strategy):
    name = "sr_momentum"

    def __init__(
        self,
        *,
        # --- levels
        use_previous_day: bool = True,
        use_opening_range: bool = True,
        use_round_numbers: bool = True,
        round_step: float = 1.0,
        atr_window: int = 24,
        level_atr: float = 0.15,
        # --- break and retest
        require_retest: bool = True,
        retest_bars: int = 6,
        # --- confirmation filters
        momentum_estimator: str = "session",
        momentum_span: int = 12,
        momentum_z_min: float = 1.0,
        rvol_min: float = 1.0,
        require_vwap_side: bool = True,
        # --- risk geometry (shared with trend_ratchet)
        vol_window: int = 24,
        horizon_bars: int = 12,
        stop_sigmas: float = 1.0,
        trail_sigmas: float = 1.5,
        # --- sizing
        sizing: str = "risk",
        risk_per_trade: float = 0.001,
        max_weight: float = 0.15,
        max_positions: int = 6,
        allow_long: bool = True,
        allow_short: bool = True,
        # --- session discipline
        no_entry_before: str | None = None,
        no_entry_after: str | None = "15:00",
        flat_time: str | None = "15:50",
        # --- external confirmation
        confirmation: pd.DataFrame | None = None,
        confirmation_path: str | None = None,
        confirmation_min: float = 0.0,
    ):
        if not (use_previous_day or use_opening_range or use_round_numbers):
            raise ValueError("at least one level family must be enabled")
        if not allow_long and not allow_short:
            raise ValueError("at least one of allow_long / allow_short must be enabled")
        if trail_sigmas < stop_sigmas:
            raise ValueError("trail_sigmas must be at least stop_sigmas")
        if max_positions * max_weight > 1.0 + 1e-9:
            raise ValueError("max_positions x max_weight exceeds the gross budget of 1")
        if retest_bars < 1:
            raise ValueError("retest_bars must be at least 1")
        if sizing not in SIZING_RULES:
            raise ValueError(f"sizing must be one of {SIZING_RULES}, got {sizing!r}")
        if momentum_estimator not in MOMENTUM_ESTIMATORS:
            raise ValueError(
                f"momentum_estimator must be one of {MOMENTUM_ESTIMATORS}, "
                f"got {momentum_estimator!r}"
            )

        self.use_previous_day = bool(use_previous_day)
        self.use_opening_range = bool(use_opening_range)
        self.use_round_numbers = bool(use_round_numbers)
        self.round_step = float(round_step)
        self.atr_window = int(atr_window)
        self.level_atr = float(level_atr)
        self.require_retest = bool(require_retest)
        self.retest_bars = int(retest_bars)
        self.momentum_estimator = momentum_estimator
        self.momentum_span = int(momentum_span)
        self.momentum_z_min = float(momentum_z_min)
        self.rvol_min = float(rvol_min)
        self.require_vwap_side = bool(require_vwap_side)
        self.vol_window = int(vol_window)
        self.horizon_bars = int(horizon_bars)
        self.stop_sigmas = float(stop_sigmas)
        self.trail_sigmas = float(trail_sigmas)
        self.sizing = sizing
        self.risk_per_trade = float(risk_per_trade)
        self.max_weight = float(max_weight)
        self.max_positions = int(max_positions)
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        self.no_entry_before = no_entry_before
        self.no_entry_after = no_entry_after
        self.flat_time = flat_time
        if confirmation is None and confirmation_path is not None:
            confirmation = load_scores(confirmation_path)
        self.confirmation = confirmation
        self.confirmation_min = float(confirmation_min)

    # ---------------------------------------------------------------- signal
    def generate(self, context: MarketContext) -> StrategySignals:
        symbols = list(context.symbols)
        panel = context.panel
        close = panel.close[symbols]
        high = panel.field("high")[symbols]
        low = panel.field("low")[symbols]

        day = session_date(close.index)
        bar_of_session = pd.Series(day.to_numpy(), index=close.index).groupby(
            day.to_numpy()
        ).cumcount()

        returns = bar_log_returns(close, within_session=True)
        volatility = seasonal_volatility(
            returns, session=day, bar_of_session=bar_of_session,
            window=self.vol_window, min_periods=max(self.vol_window // 2, 2),
        )
        sigma = volatility * np.sqrt(self.horizon_bars)
        momentum = self._momentum(returns, volatility, day)
        atr = average_true_range(high, low, close, self.atr_window, restart=pd.Series(day))
        zone = level_zone(atr, close, self.level_atr)
        rvol = self._relative_volume(panel, symbols)
        vwap_side = self._vwap_side(panel, symbols, day)

        levels = self._levels(high, low, close)
        weights, diagnostics = self._walk(
            context, close, high, low, levels, zone, momentum, rvol, vwap_side, sigma
        )
        # Published so the episode charts can draw the S/R structure the rule
        # used. `_window` copies every indicator column into the episode bars,
        # so nothing downstream has to know these exist.
        drawn = {
            f"{family}_{edge}": levels[family][k]
            for family in levels
            for k, edge in ((0, "resistance"), (1, "support"))
        }

        indicators = {
            symbol: pd.DataFrame(
                {
                    "momentum_z": momentum[symbol],
                    "atr": atr[symbol],
                    "relative_volume": rvol[symbol],
                    "vwap_side": vwap_side[symbol],
                    "horizon_sigma_bps": sigma[symbol] * 1e4,
                    "zone": zone[symbol],
                    **{name: frame[symbol] for name, frame in diagnostics.items()},
                    **{name: frame[symbol] for name, frame in drawn.items()},
                    "target_weight": weights[symbol],
                },
                index=context.index,
            )
            for symbol in symbols
        }
        return StrategySignals(
            target_weights=weights, scores=momentum[symbols], indicators=indicators
        )

    def setup_features(self, signals, context) -> dict[str, pd.DataFrame]:
        """Scale-free descriptions of the setup, for post-trade screening."""
        momentum = signals.stack("momentum_z")
        return {
            "momentum_z": momentum,
            "abs_momentum_z": momentum.abs(),
            "relative_volume": signals.stack("relative_volume"),
            "vwap_side": signals.stack("vwap_side"),
            "horizon_sigma_bps": signals.stack("horizon_sigma_bps"),
        }

    # ------------------------------------------------------------ ingredients
    def _momentum(self, returns, volatility, day) -> pd.DataFrame:
        """Standardised drift, by whichever estimator the config selected.

        ``session`` is ``sum(r)/(sigma*sqrt(n))`` over every bar since the open.
        That makes it a statement about where price sits relative to the open,
        not about where it is going: a large early move fixes its sign for the
        rest of the session however the price behaves afterwards. ``ewma``
        weights recent returns more heavily and can turn around within the day,
        at the cost of reacting to moves that do not persist.
        """
        if self.momentum_estimator == "ewma":
            return ewma_drift_zscore(
                returns, self.momentum_span, volatility=volatility, restart=day
            )
        return session_drift_zscore(returns, volatility=volatility, restart=day)

    def _levels(self, high, low, close) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
        """Resistance/support pairs from each enabled family."""
        levels = {}
        if self.use_previous_day:
            levels["previous_day"] = previous_day_levels(high, low)
        if self.use_opening_range:
            levels["opening_range"] = opening_range_levels(high, low)
        if self.use_round_numbers:
            # Bracketing the *current* close would make the level unbreakable by
            # construction: the round number above today's price is always above
            # today's price. The reference has to be the previous bar's brackets,
            # which is also the only version knowable before this bar prints.
            above, below = nearest_round_levels(close, self.round_step)
            levels["round"] = (above.shift(1), below.shift(1))
        return levels

    def _relative_volume(self, panel, symbols) -> pd.DataFrame:
        """Dollar volume against its own recent median — a participation filter."""
        dollar = panel.close[symbols] * panel.volume[symbols]
        typical = dollar.rolling(78 * 5, min_periods=78).median().replace(0.0, np.nan)
        return dollar / typical

    def _vwap_side(self, panel, symbols, day) -> pd.DataFrame:
        """+1 above the session-to-date VWAP, -1 below."""
        close = panel.close[symbols]
        typical = (panel.field("high")[symbols] + panel.field("low")[symbols] + close) / 3.0
        groups = day.to_numpy()
        traded = (typical * panel.volume[symbols]).groupby(groups).cumsum()
        shares = panel.volume[symbols].groupby(groups).cumsum()
        vwap = traded / shares.replace(0.0, np.nan)
        return np.sign(close - vwap).fillna(0.0)

    # -------------------------------------------------------------- the walk
    def _walk(self, context, close, high, low, levels, zone, momentum, rvol, vwap_side, sigma):
        """Bar loop: break -> retest -> confirm -> hold behind the barrier."""
        symbols = list(context.symbols)
        n_bars, n_symbols = len(context.index), len(symbols)

        price = close.to_numpy(dtype=float)
        highs = high.to_numpy(dtype=float)
        lows = low.to_numpy(dtype=float)
        band = zone.to_numpy(dtype=float)
        mom = momentum.to_numpy(dtype=float)
        vol_rel = rvol.to_numpy(dtype=float)
        side = vwap_side.to_numpy(dtype=float)
        risk = sigma.to_numpy(dtype=float)
        printed = context.panel.traded[symbols].to_numpy(dtype=bool)
        eligible = context.tradable[symbols].to_numpy(dtype=bool)

        resistance = np.stack([levels[k][0].to_numpy(dtype=float) for k in levels], axis=2)
        support = np.stack([levels[k][1].to_numpy(dtype=float) for k in levels], axis=2)

        confirm = self._confirmation_array(context)
        # Two blackouts, both purely clock-based and so causal by construction:
        # one for the unsettled open, one to stop opening risk near the close.
        blocked = self._clock(context.index, self.no_entry_after) | ~self._clock(
            context.index, self.no_entry_before, default=True
        )
        flatten = self._clock(context.index, self.flat_time)

        position = np.zeros(n_symbols, dtype=int)
        entry_price = np.zeros(n_symbols)
        extreme = np.zeros(n_symbols)
        risk_unit = np.zeros(n_symbols)
        trail_unit = np.zeros(n_symbols)
        weight = np.zeros(n_symbols)
        # break state: direction, the level broken, and bars since the break
        broke = np.zeros(n_symbols, dtype=int)
        broke_level = np.full(n_symbols, np.nan)
        broke_age = np.zeros(n_symbols, dtype=int)
        retested = np.zeros(n_symbols, dtype=bool)

        out = np.zeros((n_bars, n_symbols))
        candidates = np.zeros((n_bars, n_symbols))
        # The level the rule was actually watching on each bar, so a chart can
        # draw what the decision saw instead of a plausible-looking redrawing.
        watched = np.full((n_bars, n_symbols), np.nan)
        watched_side = np.zeros((n_bars, n_symbols))
        watched_age = np.full((n_bars, n_symbols), np.nan)

        for i in range(n_bars):
            open_now = position != 0
            if open_now.any():
                favourable = np.where(position > 0, price[i], -price[i])
                signed_entry = np.where(position > 0, entry_price, -entry_price)
                extreme = np.where(open_now, np.maximum(extreme, favourable), extreme)
                stop = np.maximum(signed_entry - risk_unit, extreme - trail_unit)
                closing = (
                    (open_now & (favourable <= stop))
                    | flatten[i]
                    | ~np.isfinite(price[i])
                )
                position = np.where(closing, FLAT, position)
                weight = np.where(closing, 0.0, weight)

            broke, broke_level, broke_age, retested = self._track_break(
                i, price, highs, lows, resistance, support, band,
                broke, broke_level, broke_age, retested, position,
            )
            # Snapshot before the entry block, which clears `broke` on opening.
            watched[i] = np.where(broke != 0, broke_level, np.nan)
            watched_side[i] = broke
            watched_age[i] = np.where(broke != 0, broke_age, np.nan)

            if not flatten[i] and not blocked[i]:
                direction = self._entry_direction(
                    i, price, broke, broke_level, retested, mom, vol_rel, side,
                    eligible, printed, position, confirm,
                )
                candidates[i] = direction
                opening = direction != FLAT
                if opening.any():
                    opening = self._respect_book_limit(opening, position, mom[i])
                if opening.any():
                    size = self._entry_weight(risk[i])
                    position = np.where(opening, direction, position)
                    entry_price = np.where(opening, price[i], entry_price)
                    extreme = np.where(
                        opening, np.where(direction > 0, price[i], -price[i]), extreme
                    )
                    risk_unit = np.where(opening, self.stop_sigmas * risk[i] * price[i], risk_unit)
                    trail_unit = np.where(opening, self.trail_sigmas * risk[i] * price[i], trail_unit)
                    weight = np.where(opening, size, weight)
                    broke = np.where(opening, 0, broke)

            out[i] = position * weight

        index, columns = context.index, symbols
        frame = lambda values: pd.DataFrame(values, index=index, columns=columns)
        return frame(out), {
            "candidate": frame(candidates),
            "watched_level": frame(watched),
            "watched_side": frame(watched_side),
            "watched_age": frame(watched_age),
        }

    def _track_break(self, i, price, highs, lows, resistance, support, band,
                     broke, broke_level, broke_age, retested, position):
        """Maintain, per symbol, the most recent unconsumed break and its retest."""
        p, d = price[i], band[i]

        above = resistance[i] + d[:, None]
        below = support[i] - d[:, None]
        # -inf / +inf sentinels rather than NaN, so an all-empty row is an
        # ordinary "no level broken" instead of a warning.
        highest = np.where(p[:, None] > above, resistance[i], -np.inf).max(axis=1)
        lowest = np.where(p[:, None] < below, support[i], np.inf).min(axis=1)
        broke_up = np.where(np.isfinite(highest), highest, np.nan)
        broke_down = np.where(np.isfinite(lowest), lowest, np.nan)

        # A break registers once, when price first moves beyond the level, and
        # then stays live while the retest window runs. Testing only "price is
        # beyond a level" would re-register it on every subsequent bar, reset
        # broke_age to 0 and clear `retested` — which collapses
        # break -> come back -> retest -> hold into a single-bar test of
        # "price is beyond the level and this bar straddles it".
        already_up = (broke == LONG) & (broke_level == broke_up)
        already_down = (broke == SHORT) & (broke_level == broke_down)
        fresh_up = np.isfinite(broke_up) & (position == FLAT) & ~already_up
        fresh_down = (
            np.isfinite(broke_down) & (position == FLAT) & ~already_down & ~fresh_up
        )

        broke = np.where(fresh_up, LONG, np.where(fresh_down, SHORT, broke))
        broke_level = np.where(fresh_up, broke_up, np.where(fresh_down, broke_down, broke_level))
        broke_age = np.where(fresh_up | fresh_down, 0, broke_age + 1)
        retested = np.where(fresh_up | fresh_down, False, retested)
        # A break that has not yet been retested but whose level price has left
        # entirely is dead; without this it would sit at its stale level.
        broke = np.where(np.isnan(broke_level), 0, broke)

        # a retest is price coming back into the zone around the broken level
        touched = (lows[i] <= broke_level + d) & (highs[i] >= broke_level - d)
        retested = retested | (touched & (broke != 0))

        expired = broke_age > self.retest_bars
        broke = np.where(expired, 0, broke)
        return broke, broke_level, broke_age, retested

    def _entry_direction(self, i, price, broke, broke_level, retested, mom, vol_rel,
                         side, eligible, printed, position, confirm):
        """A held break, retested, with momentum, participation and context agreeing."""
        holding_side = (
            ((broke == LONG) & (price[i] > broke_level))
            | ((broke == SHORT) & (price[i] < broke_level))
        )
        confirmed = retested if self.require_retest else np.ones_like(retested)
        ready = (position == FLAT) & eligible[i] & printed[i] & confirmed & holding_side

        strong = np.isfinite(mom[i]) & (np.abs(mom[i]) >= self.momentum_z_min)
        participating = np.isfinite(vol_rel[i]) & (vol_rel[i] >= self.rvol_min)

        long_ok = ready & (broke == LONG) & strong & (mom[i] > 0) & participating
        short_ok = ready & (broke == SHORT) & strong & (mom[i] < 0) & participating
        if self.require_vwap_side:
            long_ok &= side[i] > 0
            short_ok &= side[i] < 0
        if confirm is not None:
            long_ok &= confirm[i] >= self.confirmation_min
            short_ok &= confirm[i] <= -self.confirmation_min

        direction = np.where(long_ok & self.allow_long, LONG, FLAT)
        if self.allow_short:
            direction = np.where(short_ok, SHORT, direction)
        return direction

    def _confirmation_array(self, context) -> np.ndarray | None:
        if self.confirmation is None:
            return None
        aligned = self.confirmation.reindex(
            index=context.index, columns=list(context.symbols)
        )
        return aligned.to_numpy(dtype=float)

    def _respect_book_limit(self, opening, position, strength):
        free = self.max_positions - int((position != FLAT).sum())
        candidates = np.flatnonzero(opening)
        if free <= 0:
            return np.zeros_like(opening)
        if len(candidates) <= free:
            return opening
        best = candidates[np.argsort(-np.abs(np.nan_to_num(strength[candidates])))[:free]]
        limited = np.zeros_like(opening)
        limited[best] = True
        return limited

    def _entry_weight(self, sigma):
        """Capital per entry, by whichever rule the config selected.

        ``risk`` solves `weight * stop_distance = risk_per_trade`, so every
        trade risks the same fraction of equity between entry and stop. That is
        the textbook rule and it is correct when the edge does not depend on
        volatility. Here it does, so ``equal`` is offered as the one-parameter
        alternative: every entry gets ``max_weight`` and nothing is scaled.
        """
        if self.sizing == "equal":
            return np.where(np.isfinite(sigma) & (sigma > 0), self.max_weight, 0.0)

        distance = self.stop_sigmas * sigma
        with np.errstate(divide="ignore", invalid="ignore"):
            size = np.where(distance > 0, self.risk_per_trade / distance, 0.0)
        return np.clip(np.nan_to_num(size), 0.0, self.max_weight)

    @staticmethod
    def _clock(index, cutoff: str | None, *, default: bool = False) -> np.ndarray:
        """Bars at or after an exchange-local wall-clock time.

        ``default`` is what an unset cutoff means: False for a deadline that has
        not been set, True for a gate that everything passes.
        """
        if cutoff is None:
            return np.full(len(index), default, dtype=bool)
        return at_or_after_market_time(index, dt.time.fromisoformat(cutoff)).to_numpy()
