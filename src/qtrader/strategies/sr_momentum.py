"""Break-and-retest of ex-ante S/R levels, confirmed by momentum.

Implements the continuation sleeve of `docs/research/deep-research-report.md`:
a level is broken, the break is retested and holds, momentum agrees, and the
position is then released by the same volatility barrier `trend_ratchet` uses.

    break        close beyond L by more than the level zone
    retest       price returns into [L - d, L + d] within retest_bars
    hold         and closes back on the breakout side
    acceptance   optional subsequent closes continue in the breakout direction
    momentum     drift z-score agrees with the breakout direction
    participation relative volume at the break clears rvol_min
    context      price on the breakout side of the session VWAP

The initial loss barrier may be capped in ATR units without changing the wider
horizon-volatility ratchet that releases a trend after entry.

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

from collections.abc import Mapping

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
from ..features.multiframe import align_to_fine, fine_drift_zscore
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
#: Key that supplies the width for a symbol a fitted mapping does not name.
#: Required, so a mapping can never silently give an unfitted symbol whatever
#: the class default happens to be.
DEFAULT_WIDTH_KEY = "default"


def _as_width(value):
    """A scalar width, or a validated per-symbol mapping of widths."""
    if isinstance(value, Mapping):
        if DEFAULT_WIDTH_KEY not in value:
            raise ValueError(
                f"a per-symbol width mapping must carry a {DEFAULT_WIDTH_KEY!r} "
                "entry for symbols it does not name"
            )
        widths = {str(k): float(v) for k, v in value.items()}
        if any(w <= 0 for w in widths.values()):
            raise ValueError("every width must be positive")
        return widths
    width = float(value)
    if width <= 0:
        raise ValueError("every width must be positive")
    return width


def _check_widths(trail, stop) -> None:
    """The ratchet may never be tighter than the initial stop, per symbol.

    Compared symbol by symbol rather than by extremes: a universe where one
    name is legitimately narrow all round must not be rejected because another
    name is legitimately wide all round.
    """
    trail, stop = _as_width(trail), _as_width(stop)
    named = set()
    for value in (trail, stop):
        if isinstance(value, Mapping):
            named |= set(value) - {DEFAULT_WIDTH_KEY}

    def at(value, symbol):
        if not isinstance(value, Mapping):
            return value
        return value.get(symbol, value[DEFAULT_WIDTH_KEY])

    for symbol in sorted(named) + [DEFAULT_WIDTH_KEY]:
        if at(trail, symbol) < at(stop, symbol):
            where = "" if symbol == DEFAULT_WIDTH_KEY else f" for {symbol}"
            raise ValueError(f"trail_sigmas must be at least stop_sigmas{where}")

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
        acceptance_bars: int = 0,
        # --- confirmation filters
        momentum_estimator: str = "session",
        momentum_span: int = 12,
        momentum_z_min: float = 1.0,
        fine_momentum_z_min: float | None = None,
        fine_span: int = 15,
        fine_vol_window: int = 60,
        coarse_trend_z_min: float | None = None,
        coarse_span: int = 12,
        coarse_vol_window: int = 24,
        rvol_min: float = 1.0,
        require_vwap_side: bool = True,
        # --- risk geometry (shared with trend_ratchet)
        vol_window: int = 24,
        horizon_bars: int = 12,
        stop_sigmas: float | Mapping[str, float] = 1.0,
        initial_stop_atr: float | None = None,
        hard_stop_bps: float | None = None,
        trail_sigmas: float | Mapping[str, float] = 1.5,
        max_giveback: float | None = None,
        exhaustion_z: float | None = None,
        exhaustion_decay: float | None = None,
        exhaustion_source: str = "decision",
        exhaustion_keep: float = 0.5,
        exit_on_reversal: float | None = None,
        reversal_bars: int = 1,
        reverse_on_reversal: bool = False,
        exit_on_opposite_signal: bool = False,
        track_breaks_while_held: bool = False,
        allow_add_back: bool = False,
        # --- sizing
        sizing: str = "risk",
        risk_per_trade: float = 0.001,
        max_weight: float = 0.15,
        max_positions: int = 6,
        displace_margin: float | None = None,
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
        # --- external entry veto
        entry_veto: pd.DataFrame | None = None,
        veto_consumes_setup: bool = False,
    ):
        if not (use_previous_day or use_opening_range or use_round_numbers):
            raise ValueError("at least one level family must be enabled")
        if not allow_long and not allow_short:
            raise ValueError("at least one of allow_long / allow_short must be enabled")
        _check_widths(trail_sigmas, stop_sigmas)
        if displace_margin is not None and displace_margin <= 0.0:
            raise ValueError("displace_margin must be positive")
        if hard_stop_bps is not None and hard_stop_bps <= 0.0:
            raise ValueError("hard_stop_bps must be positive")
        if max_giveback is not None and not 0.0 < max_giveback < 1.0:
            raise ValueError("max_giveback must lie strictly between 0 and 1")
        if exhaustion_z is not None and exhaustion_z < 0.0:
            raise ValueError("exhaustion_z must be non-negative")
        if exhaustion_decay is not None and not 0.0 < exhaustion_decay < 1.0:
            raise ValueError("exhaustion_decay must lie strictly between 0 and 1")
        if exhaustion_source not in ("decision", "coarse"):
            raise ValueError("exhaustion_source must be 'decision' or 'coarse'")
        if not 0.0 <= exhaustion_keep < 1.0:
            raise ValueError("exhaustion_keep must lie in [0, 1)")
        if exit_on_reversal is not None and exit_on_reversal < 0.0:
            raise ValueError("exit_on_reversal must be non-negative")
        if reversal_bars < 1:
            raise ValueError("reversal_bars must be at least 1")
        if reverse_on_reversal and exit_on_reversal is None:
            raise ValueError("reverse_on_reversal needs exit_on_reversal to be set")
        if max_positions * max_weight > 1.0 + 1e-9:
            raise ValueError("max_positions x max_weight exceeds the gross budget of 1")
        if retest_bars < 1:
            raise ValueError("retest_bars must be at least 1")
        if acceptance_bars < 0:
            raise ValueError("acceptance_bars must be non-negative")
        if initial_stop_atr is not None and initial_stop_atr <= 0.0:
            raise ValueError("initial_stop_atr must be positive")
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
        self.acceptance_bars = int(acceptance_bars)
        self.momentum_estimator = momentum_estimator
        self.momentum_span = int(momentum_span)
        self.momentum_z_min = float(momentum_z_min)
        self.fine_momentum_z_min = (
            None if fine_momentum_z_min is None else float(fine_momentum_z_min)
        )
        self.fine_span = int(fine_span)
        self.fine_vol_window = int(fine_vol_window)
        self.coarse_trend_z_min = (
            None if coarse_trend_z_min is None else float(coarse_trend_z_min)
        )
        self.coarse_span = int(coarse_span)
        self.coarse_vol_window = int(coarse_vol_window)
        self.rvol_min = float(rvol_min)
        self.require_vwap_side = bool(require_vwap_side)
        self.vol_window = int(vol_window)
        self.horizon_bars = int(horizon_bars)
        self.stop_sigmas = _as_width(stop_sigmas)
        self.initial_stop_atr = (
            None if initial_stop_atr is None else float(initial_stop_atr)
        )
        # A ceiling on the initial loss that no volatility reading can lift.
        # R27: on a gap-down open sigma_H read 314 bps against 88-106 bps four
        # hours later, which put the stop 2.8 points beyond the session's high
        # and turned one trade into -490 bps. A cap expressed in bps cannot be
        # inflated by the same estimate it is meant to bound.
        self.hard_stop_bps = None if hard_stop_bps is None else float(hard_stop_bps)
        self.trail_sigmas = _as_width(trail_sigmas)
        self.max_giveback = None if max_giveback is None else float(max_giveback)
        self.exhaustion_z = None if exhaustion_z is None else float(exhaustion_z)
        self.exhaustion_decay = (
            None if exhaustion_decay is None else float(exhaustion_decay)
        )
        self.exhaustion_source = exhaustion_source
        self.exhaustion_keep = float(exhaustion_keep)
        self.exit_on_reversal = (
            None if exit_on_reversal is None else float(exit_on_reversal)
        )
        self.reversal_bars = int(reversal_bars)
        self.reverse_on_reversal = bool(reverse_on_reversal)
        self.exit_on_opposite_signal = bool(exit_on_opposite_signal)
        # Whether the level machine keeps running while a position is open.
        # Off, a symbol goes blind the moment it is held: R27 measured a
        # 29-point rally against an open short during which not one break
        # was ever registered, so the entry rule never even evaluated it.
        self.track_breaks_while_held = bool(track_breaks_while_held)
        self.allow_add_back = bool(allow_add_back)
        self.sizing = sizing
        self.risk_per_trade = float(risk_per_trade)
        self.max_weight = float(max_weight)
        self.max_positions = int(max_positions)
        self.displace_margin = (
            None if displace_margin is None else float(displace_margin)
        )
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        self.no_entry_before = no_entry_before
        self.no_entry_after = no_entry_after
        self.flat_time = flat_time
        if confirmation is None and confirmation_path is not None:
            confirmation = load_scores(confirmation_path)
        self.confirmation = confirmation
        self.confirmation_min = float(confirmation_min)
        # A `timestamp x symbol` boolean frame: True refuses a NEW position in
        # that symbol on that bar. It is deliberately opaque — the strategy does
        # not know or care what produced it — so a regime detector can be tested
        # as a gate without any of its logic entering this class. It vetoes
        # openings only: an existing position is still managed and exited by the
        # normal rules, because forcing a flat on a state change would be a
        # different experiment.
        self.entry_veto = entry_veto
        # Whether a setup seen while vetoed is destroyed or merely postponed.
        # Postponing looks wrong for the reason the opening clock gate records —
        # the strategy takes the same move once the veto lifts, later and at a
        # worse price, and those late re-entries measured -27.7 bps against the
        # baseline trades they replaced on m5_test.
        #
        # Consuming it nonetheless measured WORSE (gross +2.58 -> +2.37 bps) and
        # produced MORE new trades, not fewer (18 -> 59): freeing the slot sooner
        # lets the book admit a substitute. Neither behaviour is defensible on
        # the evidence, so the default is the one R22's published numbers were
        # measured with, and the switch exists to keep asking.
        self.veto_consumes_setup = bool(veto_consumes_setup)

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
        fine_momentum = self._fine_momentum(context, symbols)
        coarse_trend = None
        if self.coarse_trend_z_min is not None or self.exhaustion_source == "coarse":
            coarse_trend = self._coarse_trend(context, symbols)
        if self.coarse_trend_z_min is not None:
            # The higher timeframe as context only: it never opens a trade, it
            # only refuses one that disagrees with the larger move.
            fine_momentum = coarse_trend
        atr = average_true_range(high, low, close, self.atr_window, restart=pd.Series(day))
        zone = level_zone(atr, close, self.level_atr)
        initial_stop = pd.DataFrame(
            self._initial_stop_fraction(
                sigma.to_numpy(dtype=float),
                atr.to_numpy(dtype=float),
                close.to_numpy(dtype=float),
                # atleast_2d makes a scalar (1,1) and a per-symbol vector
                # (1, n_symbols); both broadcast over the bar axis.
                width=np.atleast_2d(
                    self._width(self.stop_sigmas, list(close.columns))
                ),
            ),
            index=close.index,
            columns=close.columns,
        )
        rvol = self._relative_volume(panel, symbols)
        vwap_side = self._vwap_side(panel, symbols, day)

        levels = self._levels(high, low, close)
        weights, diagnostics = self._walk(
            context, close, high, low, levels, zone, momentum, rvol, vwap_side,
            sigma, initial_stop, fine_momentum, coarse_trend,
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
                    "initial_stop_bps": initial_stop[symbol] * 1e4,
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
            "initial_stop_bps": signals.stack("initial_stop_bps"),
        }

    # ------------------------------------------------------------ ingredients
    def _fine_momentum(self, context, symbols) -> pd.DataFrame | None:
        """Drift on the finer grid, reported at each coarse bar's close.

        A five-minute bar only shows a move once five minutes of it have
        happened, which on this universe is often most of the move. The finer
        series sees it sooner; it also sees far more that is not a move at all,
        which is why it confirms rather than triggers.
        """
        if self.fine_momentum_z_min is None:
            return None
        if context.fine_panel is None:
            raise ValueError(
                "fine_momentum_z_min needs a finer timeframe; set data.fine_timeframe"
            )
        return fine_drift_zscore(
            context.fine_panel, symbols, context.index,
            span=self.fine_span, volatility_window=self.fine_vol_window,
        )

    def _coarse_trend(self, context, symbols) -> pd.DataFrame:
        """Drift on the coarser grid, reported on the decision grid.

        Used when the strategy itself runs on the finer bars and the coarser
        series is the trend backdrop. The value of a coarse bar is only visible
        once that bar has closed — `align_to_fine` enforces it — so a decision
        never sees a candle that is still forming.
        """
        if context.fine_panel is None:
            raise ValueError(
                "coarse_trend_z_min needs a second timeframe; set data.fine_timeframe"
            )
        coarse = context.fine_panel
        close = coarse.close[symbols]
        day = pd.Series(session_date(close.index).to_numpy(), index=close.index)
        returns = bar_log_returns(close, within_session=True)
        volatility = returns.rolling(
            self.coarse_vol_window, min_periods=max(self.coarse_vol_window // 3, 5)
        ).std()
        z = ewma_drift_zscore(returns, self.coarse_span, volatility=volatility, restart=day)
        return align_to_fine(z, context.index)

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
    def _walk(
        self, context, close, high, low, levels, zone, momentum, rvol,
        vwap_side, sigma, initial_stop, fine_momentum=None, coarse_trend=None,
    ):
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
        stop_distance = initial_stop.to_numpy(dtype=float)
        printed = context.panel.traded[symbols].to_numpy(dtype=bool)
        eligible = context.tradable[symbols].to_numpy(dtype=bool)

        resistance = np.stack([levels[k][0].to_numpy(dtype=float) for k in levels], axis=2)
        support = np.stack([levels[k][1].to_numpy(dtype=float) for k in levels], axis=2)

        fine = (
            None if fine_momentum is None
            else fine_momentum.reindex(
                index=context.index, columns=symbols
            ).to_numpy(dtype=float)
        )
        # Which series the exhaustion rule watches. The decision grid's own
        # statistic peaks at the entry by construction — the entry required it
        # to be strong — so a decay test on it fires within a handful of bars:
        # measured at a median of 7 bars into an 88-bar position, at +2.8 bps,
        # on trades whose eventual best was +18.6 bps. The coarser series moves
        # slowly enough to mean "the move is over" rather than "the last minute
        # was quiet".
        exhaustion_mom = mom
        if self.exhaustion_source == "coarse":
            if coarse_trend is None:
                raise ValueError(
                    "exhaustion_source='coarse' needs a second timeframe; "
                    "set data.fine_timeframe"
                )
            exhaustion_mom = coarse_trend.reindex(
                index=context.index, columns=symbols
            ).to_numpy(dtype=float)

        # Exit widths on the symbol axis. A mapping lets a width fitted inside a
        # training fold vary by symbol; a float behaves exactly as before.
        trail_width = self._width(self.trail_sigmas, symbols)
        confirm = self._confirmation_array(context)
        # Two blackouts, both purely clock-based and so causal by construction.
        # The opening gate has stronger semantics than the closing deadline:
        # setups observed before it opens are consumed, not held pending for the
        # first admitted bar. Otherwise the clock gate merely changes the
        # timestamp of an opening decision instead of waiting for new information.
        opening_blocked = ~self._clock(
            context.index, self.no_entry_before, default=True
        )
        closing_blocked = self._clock(context.index, self.no_entry_after)
        blocked = closing_blocked | opening_blocked
        # Per-symbol, unlike the clocks: a regime is a property of one series.
        # Reindexed rather than assumed aligned, and missing values are NOT a
        # veto, so a symbol the detector never covered trades as it always did.
        vetoed = (
            np.zeros((n_bars, n_symbols), dtype=bool)
            if self.entry_veto is None
            else self.entry_veto.reindex(index=context.index, columns=symbols)
            .fillna(False).to_numpy(dtype=bool)
        )
        flatten = self._clock(context.index, self.flat_time)
        sessions = session_date(context.index).to_numpy()

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
        # Number of consecutive post-break closes that moved farther in the
        # breakout direction. The break bar itself never counts: acceptance is
        # evidence that arrived after the event, not another spelling of it.
        acceptance = np.zeros(n_symbols, dtype=int)
        # A blocked or already-traded break remains consumed while price stays
        # beyond that same level. It becomes eligible again only after price
        # invalidates it and subsequently re-breaks, or after a different level
        # is broken. This prevents a tight stop from repeatedly re-entering the
        # same event while price never reset through its boundary.
        discarded_side = np.zeros(n_symbols, dtype=int)
        discarded_level = np.full(n_symbols, np.nan)
        # consecutive bars the trend statistic has spent against an open position
        against = np.zeros(n_symbols, dtype=int)
        # whether a position has already been scaled down once on exhaustion
        scaled = np.zeros(n_symbols, dtype=bool)
        # best |momentum| seen since the position opened, for peak-relative decay
        peak_mom = np.zeros(n_symbols)

        out = np.zeros((n_bars, n_symbols))
        candidates = np.zeros((n_bars, n_symbols))
        # The level the rule was actually watching on each bar, so a chart can
        # draw what the decision saw instead of a plausible-looking redrawing.
        watched = np.full((n_bars, n_symbols), np.nan)
        watched_side = np.zeros((n_bars, n_symbols))
        watched_age = np.full((n_bars, n_symbols), np.nan)
        watched_acceptance = np.full((n_bars, n_symbols), np.nan)

        for i in range(n_bars):
            if i == 0 or sessions[i] != sessions[i - 1]:
                # Intraday position/setup state never crosses an overnight
                # boundary. The ordinary flat_time handles full sessions; this
                # reset also covers exchange early closes that have no 15:50 bar.
                position.fill(FLAT)
                entry_price.fill(0.0)
                extreme.fill(0.0)
                risk_unit.fill(0.0)
                trail_unit.fill(0.0)
                weight.fill(0.0)
                against.fill(0)
                broke.fill(FLAT)
                broke_level.fill(np.nan)
                broke_age.fill(0)
                retested.fill(False)
                acceptance.fill(0)
                discarded_side.fill(FLAT)
                discarded_level.fill(np.nan)

            open_now = position != 0
            if open_now.any():
                favourable = np.where(position > 0, price[i], -price[i])
                signed_entry = np.where(position > 0, entry_price, -entry_price)
                extreme = np.where(open_now, np.maximum(extreme, favourable), extreme)
                stop = np.maximum(signed_entry - risk_unit, extreme - trail_unit)

                if self.max_giveback is not None:
                    # The ratchet's give-back is `trail_sigmas * sigma_H`, fixed
                    # at entry. On a volatile open that is a very large number in
                    # absolute terms, so a trade can be up several hundred bps
                    # and still surrender nearly all of it before the trail is
                    # touched. This caps the give-back as a fraction of the
                    # profit actually achieved, which is scale-free.
                    #
                    # Armed only once the position is up more than it risked, so
                    # it cannot close a trade that has never been meaningfully
                    # ahead — and so it needs no threshold of its own.
                    peak = np.maximum(extreme - signed_entry, 0.0)
                    keep = signed_entry + (1.0 - self.max_giveback) * peak
                    stop = np.where(peak >= risk_unit, np.maximum(stop, keep), stop)
                closing = (
                    (open_now & (favourable <= stop))
                    | flatten[i]
                    | ~np.isfinite(price[i])
                )

                if self.exit_on_reversal is not None:
                    # The position is held on a claim about the trend. When the
                    # same statistic that opened it has moved to the other side,
                    # the claim is no longer true and the position should not
                    # survive on exit geometry alone — otherwise the strategy is
                    # simultaneously asserting "up" and holding a short.
                    #
                    # Note this needs an estimator that CAN reverse within a
                    # session: `session` drift accumulates from the open and
                    # keeps its sign after a large early move (R10 §5c), so it
                    # will almost never trigger this.
                    turned = np.isfinite(mom[i]) & (
                        ((position == LONG) & (mom[i] <= -self.exit_on_reversal))
                        | ((position == SHORT) & (mom[i] >= self.exit_on_reversal))
                    )
                    # A z-score crosses zero constantly, so a single bar on the
                    # other side is noise, not a change of trend. The reversal
                    # has to persist to count as a judgement.
                    against = np.where(turned & open_now, against + 1, 0)
                    reversed_now = open_now & (against >= self.reversal_bars)
                    closing = closing | reversed_now

                    if self.reverse_on_reversal:
                        # Stop and reverse: the statistic now points the other
                        # way, so take that side rather than standing aside and
                        # waiting for a fresh break to form. Still subject to
                        # everything that governs any entry — the symbol must be
                        # eligible and have printed, and the session clocks
                        # still refuse new risk near the close.
                        flipping = (
                            reversed_now & eligible[i] & printed[i] & ~vetoed[i]
                            & ~flatten[i] & ~blocked[i]
                            & np.isfinite(risk[i]) & (risk[i] > 0)
                            & np.isfinite(price[i]) & (price[i] > 0)
                        )
                        if flipping.any():
                            flipped = -position
                            size = self._entry_weight(stop_distance[i])
                            position = np.where(flipping, flipped, position)
                            weight = np.where(flipping, size, weight)
                            entry_price = np.where(flipping, price[i], entry_price)
                            extreme = np.where(
                                flipping,
                                np.where(flipped > 0, price[i], -price[i]),
                                extreme,
                            )
                            risk_unit = np.where(
                                flipping, stop_distance[i] * price[i], risk_unit
                            )
                            trail_unit = np.where(
                                flipping, trail_width * risk[i] * price[i], trail_unit
                            )
                            scaled = np.where(flipping, False, scaled)
                            against = np.where(flipping, 0, against)
                            # already handled; do not also close it below
                            closing = closing & ~flipping
                current = exhaustion_mom[i]
                peak_mom = np.where(
                    open_now & np.isfinite(current),
                    np.maximum(peak_mom, np.abs(current)),
                    peak_mom,
                )

                if self.exhaustion_z is not None or self.exhaustion_decay is not None:
                    # Momentum exhaustion: the move that justified the position
                    # has decayed, but has not reversed. Take part of the profit
                    # off rather than waiting for a barrier calibrated to a much
                    # larger excursion than this trade produced.
                    #
                    # Only while ahead — this is a profit rule, not a stop — and
                    # only once per position, so a statistic hovering around the
                    # threshold cannot bleed the position away a slice at a time.
                    ahead = favourable > signed_entry
                    faded = np.zeros(n_symbols, dtype=bool)
                    if self.exhaustion_z is not None:
                        faded |= np.abs(current) < self.exhaustion_z
                    if self.exhaustion_decay is not None:
                        # Momentum has fallen back from ITS OWN best during this
                        # trade. An absolute floor cannot express "the move is
                        # over": early in a move |z| is small because the move
                        # has not happened yet, so a floor fires at the start of
                        # a run rather than the end — measured on QQQ
                        # 2025-11-20, it halved the position at 35 bps of profit
                        # immediately before a 339 bps continuation, while |z|
                        # rose to 5.8 at the actual low.
                        faded |= (peak_mom > 0) & (
                            np.abs(current) < self.exhaustion_decay * peak_mom
                        )
                    spent = (
                        open_now & ~scaled & ahead & ~closing
                        & np.isfinite(current) & faded
                    )
                    weight = np.where(spent, weight * self.exhaustion_keep, weight)
                    scaled = scaled | spent

                position = np.where(closing, FLAT, position)
                weight = np.where(closing, 0.0, weight)
                against = np.where(closing, 0, against)
                scaled = np.where(closing, False, scaled)
                peak_mom = np.where(closing, 0.0, peak_mom)

            broke, broke_level, broke_age, retested = self._track_break(
                i, price, highs, lows, resistance, support, band,
                broke, broke_level, broke_age, retested, position,
            )

            # Release a consumed setup only after price has returned through its
            # breakout boundary. A later move through the same level is then a
            # genuinely new event rather than a delayed opening signal.
            discarded_live = (
                ((discarded_side == LONG) & (price[i] > discarded_level + band[i]))
                | ((discarded_side == SHORT) & (price[i] < discarded_level - band[i]))
            )
            discarded_side = np.where(discarded_live, discarded_side, FLAT)
            discarded_level = np.where(discarded_live, discarded_level, np.nan)

            if opening_blocked[i]:
                observed = broke != FLAT
                discarded_side = np.where(observed, broke, discarded_side)
                discarded_level = np.where(observed, broke_level, discarded_level)
                # Nothing formed before the decision gate may remain as a live
                # setup. The diagnostics therefore also show no watched setup.
                broke = np.where(observed, FLAT, broke)
                broke_level = np.where(observed, np.nan, broke_level)
                broke_age = np.where(observed, 0, broke_age)
                retested = np.where(observed, False, retested)
                acceptance = np.where(observed, 0, acceptance)

            if self.veto_consumes_setup and vetoed[i].any():
                # Same semantics as the opening clock above, per symbol: a break
                # observed while the veto is up is consumed, not queued. Price
                # must invalidate the level and re-break it before it may trade.
                spent = (broke != FLAT) & vetoed[i]
                discarded_side = np.where(spent, broke, discarded_side)
                discarded_level = np.where(spent, broke_level, discarded_level)
                broke = np.where(spent, FLAT, broke)
                broke_level = np.where(spent, np.nan, broke_level)
                broke_age = np.where(spent, 0, broke_age)
                retested = np.where(spent, False, retested)
                acceptance = np.where(spent, 0, acceptance)
            else:
                inherited = (
                    (broke != FLAT)
                    & (broke == discarded_side)
                    & np.isclose(broke_level, discarded_level, equal_nan=False)
                )
                broke = np.where(inherited, FLAT, broke)
                broke_level = np.where(inherited, np.nan, broke_level)
                broke_age = np.where(inherited, 0, broke_age)
                retested = np.where(inherited, False, retested)
                acceptance = np.where(inherited, 0, acceptance)

            # Count only closes after the break bar, and only while price is
            # still accepted beyond the watched level. A sideways or adverse
            # close resets the streak rather than allowing intermittent bars to
            # add up to a misleading confirmation.
            holding_side = (
                ((broke == LONG) & (price[i] > broke_level))
                | ((broke == SHORT) & (price[i] < broke_level))
            )
            if i == 0 or sessions[i] != sessions[i - 1]:
                progressed = np.zeros(n_symbols, dtype=bool)
            else:
                progressed = (
                    ((broke == LONG) & (price[i] > price[i - 1]))
                    | ((broke == SHORT) & (price[i] < price[i - 1]))
                )
            acceptance = np.where(
                (broke != FLAT) & (broke_age > 0) & holding_side & progressed,
                acceptance + 1,
                0,
            )

            if self.allow_add_back and (position != FLAT).any():
                # The move continued and the rule would open this side again,
                # but the position is below full size after a scale-out. Put the
                # size back rather than watching the rest of the move at half
                # weight.
                #
                # The stop anchors are deliberately NOT reset: `entry_price` and
                # `extreme` still refer to the original entry, so topping up
                # cannot loosen the protection already earned on the position.
                flat = np.zeros_like(position)
                proposed = self._entry_direction(
                    i, price, broke, broke_level, retested, mom, vol_rel, side,
                    eligible, printed, flat, confirm, acceptance, fine,
                )
                full = self._entry_weight(stop_distance[i])
                topping = (
                    (position != FLAT) & (proposed == position)
                    & (weight < full - 1e-12)
                    & ~flatten[i] & ~blocked[i] & ~vetoed[i]
                    & eligible[i] & printed[i]
                )
                if topping.any():
                    weight = np.where(topping, full, weight)
                    # it may fade and be scaled out again later
                    scaled = np.where(topping, False, scaled)
                    peak_mom = np.where(topping, 0.0, peak_mom)

            if self.exit_on_opposite_signal and (position != FLAT).any():
                # Would the entry rule open the other way right now? If so the
                # strategy's reading of this symbol has reversed, and holding
                # the old position would mean asserting both directions at once.
                # Evaluated as if flat, because the real gate refuses to fire
                # while a position is open.
                flat = np.zeros_like(position)
                proposed = self._entry_direction(
                    i, price, broke, broke_level, retested, mom, vol_rel, side,
                    eligible, printed, flat, confirm, acceptance, fine,
                )
                conflicted = (position != FLAT) & (proposed == -position)
                position = np.where(conflicted, FLAT, position)
                weight = np.where(conflicted, 0.0, weight)
            # Snapshot before the entry block, which clears `broke` on opening.
            watched[i] = np.where(broke != 0, broke_level, np.nan)
            watched_side[i] = broke
            watched_age[i] = np.where(broke != 0, broke_age, np.nan)
            watched_acceptance[i] = np.where(broke != 0, acceptance, np.nan)

            if not flatten[i] and not blocked[i]:
                direction = self._entry_direction(
                    i, price, broke, broke_level, retested, mom, vol_rel, side,
                    eligible, printed, position, confirm, acceptance, fine,
                )
                direction = np.where(vetoed[i], FLAT, direction)
                candidates[i] = direction
                opening = direction != FLAT
                if opening.any():
                    # A holding is replaceable only while it is under water:
                    # displacing a winner to chase a signal is how a book churns
                    # itself flat.
                    losing = (position != FLAT) & (
                        np.where(position > 0, price[i], -price[i])
                        < np.where(position > 0, entry_price, -entry_price)
                    )
                    opening, displaced = self._resolve_book(
                        opening, position, mom[i], losing
                    )
                    if displaced.any():
                        position = np.where(displaced, FLAT, position)
                        weight = np.where(displaced, 0.0, weight)
                if opening.any():
                    size = self._entry_weight(stop_distance[i])
                    discarded_side = np.where(opening, direction, discarded_side)
                    discarded_level = np.where(opening, broke_level, discarded_level)
                    position = np.where(opening, direction, position)
                    entry_price = np.where(opening, price[i], entry_price)
                    extreme = np.where(
                        opening, np.where(direction > 0, price[i], -price[i]), extreme
                    )
                    risk_unit = np.where(opening, stop_distance[i] * price[i], risk_unit)
                    trail_unit = np.where(opening, trail_width * risk[i] * price[i], trail_unit)
                    weight = np.where(opening, size, weight)
                    scaled = np.where(opening, False, scaled)
                    peak_mom = np.where(opening, 0.0, peak_mom)
                    broke = np.where(opening, 0, broke)

            out[i] = position * weight

        index, columns = context.index, symbols
        frame = lambda values: pd.DataFrame(values, index=index, columns=columns)
        return frame(out), {
            "candidate": frame(candidates),
            "watched_level": frame(watched),
            "watched_side": frame(watched_side),
            "watched_age": frame(watched_age),
            "acceptance_count": frame(watched_acceptance),
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
        # Breaks normally register only while flat, since their only use is to
        # open a position. Detecting an opposite signal needs them to keep
        # forming underneath an open position too, so the gate is lifted only
        # when that exit is enabled — the baseline state machine is unchanged.
        watching = (
            np.ones_like(position, dtype=bool)
            if (self.exit_on_opposite_signal or self.allow_add_back
                or self.track_breaks_while_held)
            else (position == FLAT)
        )
        fresh_up = np.isfinite(broke_up) & watching & ~already_up
        fresh_down = np.isfinite(broke_down) & watching & ~already_down & ~fresh_up

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
                         side, eligible, printed, position, confirm, acceptance,
                         fine=None):
        """A held break, retested, with momentum, participation and context agreeing."""
        holding_side = (
            ((broke == LONG) & (price[i] > broke_level))
            | ((broke == SHORT) & (price[i] < broke_level))
        )
        confirmed = retested if self.require_retest else np.ones_like(retested)
        accepted = acceptance >= self.acceptance_bars
        ready = (
            (position == FLAT) & eligible[i] & printed[i] & confirmed
            & holding_side & accepted
        )

        strong = np.isfinite(mom[i]) & (np.abs(mom[i]) >= self.momentum_z_min)
        participating = np.isfinite(vol_rel[i]) & (vol_rel[i] >= self.rvol_min)

        long_ok = ready & (broke == LONG) & strong & (mom[i] > 0) & participating
        short_ok = ready & (broke == SHORT) & strong & (mom[i] < 0) & participating
        if self.require_vwap_side:
            long_ok &= side[i] > 0
            short_ok &= side[i] < 0
        if fine is not None:
            # The finer grid must agree with the direction being taken. A bar
            # where it has nothing to say (no fine data) is a refusal, not a
            # pass — the point of the filter is to require confirmation.
            floor = (self.coarse_trend_z_min if self.coarse_trend_z_min is not None
                     else self.fine_momentum_z_min)
            heard = np.isfinite(fine[i])
            long_ok &= heard & (fine[i] >= floor)
            short_ok &= heard & (fine[i] <= -floor)
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

    def _resolve_book(self, opening, position, strength, losing):
        """Which candidates open, and which holdings they replace.

        Free slots are filled strongest-first, exactly as before. Beyond that,
        `displace_margin` lets a materially stronger candidate take the place of
        a materially weaker holding that is currently losing.

        The margin has to be large. A displacement pays two round trips — the
        incumbent's exit and the replacement's entry — so at 3 bps each the new
        candidate must be worth more than 6 bps of edge over the one it evicts,
        or the book simply churns. R17 measured the alternative: with no
        displacement the book sits at its cap 83% of the time and admits trades
        no better than the ones it turns away.

        Returns ``(opening, displaced)``. The book never grows: one holding
        leaves for each candidate admitted this way.
        """
        held = position != FLAT
        free = self.max_positions - int(held.sum())
        displaced = np.zeros_like(opening)
        candidates = np.flatnonzero(opening)
        if not len(candidates):
            return opening, displaced

        ranked = candidates[np.argsort(-np.abs(np.nan_to_num(strength[candidates])))]
        admitted = np.zeros_like(opening)
        take = max(free, 0)
        admitted[ranked[:take]] = True

        queued = ranked[take:]
        if self.displace_margin is None or not len(queued):
            return admitted, displaced

        # Weakest replaceable holding first, so the cheapest eviction is used.
        evictable = np.flatnonzero(held & losing)
        evictable = evictable[np.argsort(np.abs(np.nan_to_num(strength[evictable])))]

        for candidate in queued:
            if not len(evictable):
                break
            weakest = evictable[0]
            gap = abs(strength[candidate]) - abs(strength[weakest])
            if not np.isfinite(gap) or gap < self.displace_margin:
                # `queued` is ordered strongest-first, so nothing after this
                # candidate can clear the margin either.
                break
            admitted[candidate] = True
            displaced[weakest] = True
            evictable = evictable[1:]

        return admitted, displaced

    @staticmethod
    def _width(width, symbols):
        """Broadcast a width onto the symbol axis.

        A float is returned unchanged, so every existing config keeps scalar
        arithmetic. A mapping becomes an array aligned to ``symbols``, with
        unnamed symbols taking the mapping's own default rather than the
        class's — the distinction matters when a width was fitted on a subset
        of the universe.
        """
        if not isinstance(width, Mapping):
            return width
        if symbols is None:
            return width[DEFAULT_WIDTH_KEY]
        fallback = width[DEFAULT_WIDTH_KEY]
        return np.array([width.get(s, fallback) for s in symbols], dtype=float)

    def _initial_stop_fraction(self, sigma, atr, price, *, width=None):
        """Initial loss distance as a fraction of entry reference price.

        The historical stop is ``stop_sigmas * horizon_sigma``. When an ATR
        multiplier is configured it is a cap, not a replacement: unusually
        quiet ATR data cannot widen a stop, and the independent wide trend
        ratchet remains unchanged after entry.

        ``width`` overrides the scalar with a per-symbol row vector, so a width
        fitted inside a training fold can differ by symbol without any other
        part of the geometry changing.
        """
        if width is None:
            width = self._width(self.stop_sigmas, None)
        distance = width * np.asarray(sigma, dtype=float)
        if self.initial_stop_atr is not None:
            with np.errstate(divide="ignore", invalid="ignore"):
                atr_distance = self.initial_stop_atr * np.asarray(
                    atr, dtype=float
                ) / np.asarray(price, dtype=float)
            valid_atr = np.isfinite(atr_distance) & (atr_distance > 0)
            distance = np.where(valid_atr, np.minimum(distance, atr_distance), distance)
        if self.hard_stop_bps is not None:
            # Applied last, so it binds whatever the other two produced.
            distance = np.minimum(distance, self.hard_stop_bps / 1e4)
        return distance

    def _entry_weight(self, stop_distance):
        """Capital per entry, by whichever rule the config selected.

        ``risk`` solves `weight * stop_distance = risk_per_trade`, so every
        trade risks the same fraction of equity between entry and stop. That is
        the textbook rule and it is correct when the edge does not depend on
        volatility. Here it does, so ``equal`` is offered as the one-parameter
        alternative: every entry gets ``max_weight`` and nothing is scaled.
        """
        if self.sizing == "equal":
            return np.where(
                np.isfinite(stop_distance) & (stop_distance > 0), self.max_weight, 0.0
            )

        with np.errstate(divide="ignore", invalid="ignore"):
            size = np.where(
                stop_distance > 0, self.risk_per_trade / stop_distance, 0.0
            )
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
