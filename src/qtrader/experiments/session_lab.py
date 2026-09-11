"""One trading day, one symbol, re-run cheaply while parameters are tuned.

The expensive part of a backtest is loading and aligning bars, and that does not
change when a threshold does. `SessionLab` loads once and re-runs the strategy
against the cached context, so a parameter sweep is seconds rather than minutes.

This exists in `src/` rather than in the notebook because CLAUDE.md §18 is
explicit that a notebook may explore but must never be the only copy of logic.
The notebook is a thin caller.

Warm-up matters and is easy to get wrong: `momentum`, ATR, relative volume and
the seasonal volatility profile all need history *before* the session under
study, so the loaded window starts `warmup_days` earlier. Only the target
session's trades are reported.

A name that is not in the config universe is added for this lab only. Missing
local bars are downloaded; the error is reserved for a name Alpaca does not
list (or returns nothing for).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from ..analysis.episodes import extract_episodes
from ..config import RunConfig
from ..data.alpaca_client import UnknownSymbolError, resolve_symbol
from ..data.ingest import ensure_bars
from ..data.sessions import session_date
from ..data.storage import BarStore
from ..runner import Run, build_context, execute

#: Sessions of history loaded before the one under study. Relative volume needs
#: the longest look-back (a 5-session rolling median), so this is its floor.
DEFAULT_WARMUP_DAYS = 12


@dataclass
class SessionLab:
    """A cached one-day workbench. Build once, `run(...)` as often as you like."""

    config_path: str
    symbol: str
    day: str
    warmup_days: int = DEFAULT_WARMUP_DAYS
    _context: object = field(default=None, repr=False)
    _config: RunConfig = field(default=None, repr=False)

    def __post_init__(self) -> None:
        base = RunConfig.from_yaml(self.config_path)
        target = pd.Timestamp(self.day).date()
        start = target - dt.timedelta(days=self.warmup_days * 2)  # calendar vs trading
        end = target + dt.timedelta(days=1)
        self.symbol = resolve_symbol(self.symbol)
        universe = base.universe().with_symbol(self.symbol)
        self._config = replace(
            base,
            run_id=f"{base.run_id}__lab_{target:%Y%m%d}",
            data=replace(base.data, start=str(start), end=str(end)),
        )
        store = BarStore(self._config.data_root)
        window_start = self._config.data.start_dt()
        window_end = self._config.data.end_dt()
        downloaded = ensure_bars(
            universe.all_symbols,
            window_start,
            window_end,
            timeframe=self._config.data.timeframe,
            feed=self._config.data.feed,
            store=store,
            regular_hours_only=self._config.data.regular_hours_only,
            long_lookback_for=(self.symbol,),
            on_progress=lambda s: print(f"downloaded {s}", flush=True),
        )
        if self._config.data.fine_timeframe:
            downloaded = downloaded + ensure_bars(
                universe.all_symbols,
                window_start,
                window_end,
                timeframe=self._config.data.fine_timeframe,
                feed=self._config.data.feed,
                store=store,
                regular_hours_only=self._config.data.regular_hours_only,
                long_lookback_for=(self.symbol,),
                on_progress=lambda s: print(f"downloaded {s} (fine)", flush=True),
            )
        if downloaded:
            print(
                f"stored {', '.join(downloaded)} "
                f"{self._config.data.timeframe} {self._config.data.feed}",
                flush=True,
            )
        self._context = build_context(self._config, store=store, universe=universe)
        if self.symbol not in self._context.panel.symbols:
            raise UnknownSymbolError(
                f"{self.symbol!r} is not listed on Alpaca; nothing to download"
            )
        if not len(self.session_index):
            raise ValueError(f"no bars for {self.symbol} on {self.day}")

    # ------------------------------------------------------------------ data
    @property
    def context(self):
        return self._context

    @property
    def session_index(self) -> pd.DatetimeIndex:
        """Bars belonging to the day under study, in exchange-local terms."""
        index = self._context.index
        same = session_date(index).to_numpy() == pd.Timestamp(self.day).date()
        return index[same]

    @property
    def window(self) -> tuple:
        bars = self.session_index
        return bars[0], bars[-1]

    # ------------------------------------------------------------------- run
    def run(self, **overrides) -> Run:
        """Re-run the strategy with parameter overrides, reusing the loaded bars."""
        config = self._config
        if overrides:
            config = replace(
                config, strategy=replace(config.strategy,
                                         params={**config.strategy.params, **overrides})
            )
        return execute(config, context=self._context)

    # --------------------------------------------------------------- results
    def trades(self, run: Run) -> pd.DataFrame:
        """Round trips opened on the day under study, in this symbol."""
        table = run.result.trades
        if table.empty:
            return table
        same = session_date(pd.DatetimeIndex(table["entry_time"])).to_numpy() == \
            pd.Timestamp(self.day).date()
        picked = table.loc[same & (table["symbol"] == self.symbol).to_numpy()].copy()
        for column in ("entry_time", "exit_time"):
            picked[column] = pd.DatetimeIndex(picked[column]).tz_convert("America/New_York")
        return picked

    def episodes(self, run: Run, context_bars: int = 12):
        return extract_episodes(run, context_bars=context_bars)

    def summary(self, run: Run) -> pd.Series:
        """The handful of numbers worth reading after each parameter change."""
        trades = self.trades(run)
        curve = run.result.equity_curve.loc[list(self.session_index)]
        if trades.empty:
            return pd.Series({"trades": 0, "net_pnl": 0.0, "gross_bps": float("nan"),
                              "hit_rate": float("nan"), "day_return": 0.0})
        notional = trades["shares"].abs() * trades["entry_reference"]
        return pd.Series({
            "trades": len(trades),
            "net_pnl": trades["net_pnl"].sum(),
            "gross_bps": (trades["gross_pnl"] / notional).mean() * 1e4,
            "cost_bps": (trades["costs"] / notional).mean() * 1e4,
            "hit_rate": (trades["net_pnl"] > 0).mean(),
            "day_return": curve["equity"].iloc[-1] / curve["equity"].iloc[0] - 1.0,
        })

    def triggers(self, run: Run) -> pd.DataFrame:
        """Every gate's value at the bar each trade was decided on, with pass/fail.

        Read at the **decision** bar, one execution lag before the fill, because
        that is the only information the rule had. Reading at the fill would
        show a state the strategy never saw and would quietly excuse decisions
        it could not have made.
        """
        trades = self.trades(run)
        if trades.empty:
            return pd.DataFrame()

        index = self._context.index
        position = {stamp: i for i, stamp in enumerate(index)}
        lag = run.config.execution.execution_lag_bars
        params = run.config.strategy.params
        indicators = run.result.signals.indicators[self.symbol]
        panel = self._context.panel
        close = panel.close[self.symbol]
        high = panel.field("high")[self.symbol]
        low = panel.field("low")[self.symbol]
        volume = panel.volume[self.symbol]
        days = session_date(index).to_numpy()
        target = run.result.signals.target_weights[self.symbol].to_numpy(dtype=float)

        rows = []
        for trade_number, (_, trade) in enumerate(trades.iterrows(), start=1):
            entry_at = position[pd.Timestamp(trade["entry_time"]).tz_convert("UTC")]
            active_at = entry_at - lag
            side = 1 if trade["direction"] == "LONG" else -1
            # A missing print can delay the fill after the original signal. Walk
            # back through the still-active position run to the bar that really
            # created it rather than blindly calling `fill - lag` the decision.
            at = active_at
            while at > 0 and np.sign(target[at - 1]) == side:
                at -= 1
            bar = indicators.iloc[at]
            momentum, level = bar["momentum_z"], bar["watched_level"]
            zone = bar["zone"]
            age = int(bar["watched_age"]) if np.isfinite(bar["watched_age"]) else 0
            break_at = max(at - age, 0)
            break_row = indicators.iloc[break_at]
            break_level = break_row["watched_level"]
            break_zone = break_row["zone"]
            break_close = close.iloc[break_at]
            break_threshold = break_level + side * break_zone
            decision_close = close.iloc[at]

            session_positions = np.flatnonzero(days == days[at])
            session_start = int(session_positions[0])
            elapsed = at - session_start
            slice_ = slice(session_start, at + 1)
            typical = (high.iloc[slice_] + low.iloc[slice_] + close.iloc[slice_]) / 3.0
            session_volume = volume.iloc[slice_]
            total_volume = session_volume.sum()
            vwap = float((typical * session_volume).sum() / total_volume) \
                if total_volume > 0 else float("nan")

            sigma_h_bps = float(bar["horizon_sigma_bps"])
            horizon = int(params.get("horizon_bars", 1))
            sigma_bar_bps = sigma_h_bps / np.sqrt(horizon)
            estimator = params.get("momentum_estimator", "session")
            if estimator == "ewma":
                span = int(params.get("momentum_span", 12))
                beta = 1.0 - 2.0 / (span + 1.0)
                s2 = (1.0 - beta ** (2.0 * elapsed)) / (1.0 - beta**2) \
                    if elapsed else 0.0
                momentum_scale_bps = sigma_bar_bps * np.sqrt(s2)
            else:
                momentum_scale_bps = sigma_bar_bps * np.sqrt(elapsed)
            momentum_numerator_bps = momentum * momentum_scale_bps

            dollar_volume = decision_close * volume.iloc[at]
            rvol = float(bar["relative_volume"])
            rvol_median = dollar_volume / rvol if np.isfinite(rvol) and rvol > 0 else np.nan
            atr = float(bar["atr"])
            atr_multiple = params.get("initial_stop_atr")
            atr_stop_bps = (
                float(atr_multiple) * atr / decision_close * 1e4
                if atr_multiple is not None and decision_close > 0 else np.nan
            )
            sigma_stop_bps = float(params.get("stop_sigmas", 1.0)) * sigma_h_bps
            initial_stop_bps = float(bar["initial_stop_bps"])
            stop_level = decision_close * (1.0 - side * initial_stop_bps / 1e4)
            trail_bps = float(params.get("trail_sigmas", 1.5)) * sigma_h_bps
            entry_reference = float(trade["entry_reference"])
            exit_reference = float(trade["exit_reference"])
            notional = abs(trade["shares"]) * entry_reference

            rows.append({
                "trade": trade_number,
                "decision": index[at].tz_convert("America/New_York").strftime("%H:%M"),
                "break time": index[break_at].tz_convert("America/New_York").strftime("%H:%M"),
                "fill": pd.Timestamp(trade["entry_time"]).strftime("%H:%M"),
                "dir": trade["direction"],
                "decision open": panel.field("open")[self.symbol].iloc[at],
                "decision high": high.iloc[at],
                "decision low": low.iloc[at],
                "decision close": decision_close,
                "volume": volume.iloc[at],
                "level": level,
                "zone": zone,
                "break close": break_close,
                "break threshold": break_threshold,
                "break margin bps": side * (break_close - break_threshold)
                                    / break_close * 1e4,
                "break ok": side * (break_close - break_threshold) > 0,
                "hold margin bps": side * (decision_close - level)
                                   / decision_close * 1e4,
                "side ok": bar["watched_side"] == side,
                "accept": bar["acceptance_count"],
                "accept min": params.get("acceptance_bars", 0),
                "accept ok": bar["acceptance_count"] >= params.get("acceptance_bars", 0),
                "momentum_z": momentum,
                "mom min": params.get("momentum_z_min", 0.0),
                "momentum numerator bps": momentum_numerator_bps,
                "momentum scale bps": momentum_scale_bps,
                "mom ok": abs(momentum) >= params.get("momentum_z_min", 0.0)
                          and (momentum > 0) == (side > 0),
                "rvol": rvol,
                "rvol min": params.get("rvol_min", 0.0),
                "dollar volume": dollar_volume,
                "rvol median": rvol_median,
                "rvol ok": bar["relative_volume"] >= params.get("rvol_min", 0.0),
                "session vwap": vwap,
                "vwap side": bar["vwap_side"],
                "vwap ok": (not params.get("require_vwap_side", False))
                           or bar["vwap_side"] == side,
                "ATR": atr,
                "sigma H bps": sigma_h_bps,
                "sigma stop bps": sigma_stop_bps,
                "ATR stop bps": atr_stop_bps,
                "initial stop bps": initial_stop_bps,
                "initial stop level": stop_level,
                "trail bps": trail_bps,
                "entry reference": entry_reference,
                "fill gap bps": side * (entry_reference / decision_close - 1.0) * 1e4,
                "exit reference": exit_reference,
                "hold bars": position[pd.Timestamp(trade["exit_time"]).tz_convert("UTC")]
                             - entry_at,
                "gross bps": trade["gross_pnl"]
                             / notional * 1e4,
                "cost bps": trade["costs"] / notional * 1e4,
                "net bps": trade["net_pnl"] / notional * 1e4,
            })
        return pd.DataFrame(rows).set_index("trade")

    def formula_audit(self, run: Run) -> pd.DataFrame:
        """One symbolic formula and numerical substitution per trade and rule.

        The notebook displays this long form because a wide trigger table is
        good for comparison but poor for reading one decision carefully. Values
        come from :meth:`triggers`; no strategy calculation is reimplemented in
        the notebook.
        """
        triggers = self.triggers(run)
        if triggers.empty:
            return pd.DataFrame()

        rows = []
        for trade, row in triggers.iterrows():
            side = 1 if row["dir"] == "LONG" else -1
            relation = ">" if side > 0 else "<"
            rows.extend([
                self._formula_row(
                    trade, row, "1 break",
                    "LONG: C_break > L + zone; SHORT: C_break < L - zone",
                    f"{row['break close']:.3f} {relation} {row['break threshold']:.3f} "
                    f"(margin {row['break margin bps']:+.1f} bps)",
                    row["break ok"],
                ),
                self._formula_row(
                    trade, row, "2 hold side",
                    "LONG: C_decision > L; SHORT: C_decision < L",
                    f"{row['decision close']:.3f} {relation} {row['level']:.3f} "
                    f"(margin {row['hold margin bps']:+.1f} bps)",
                    row["side ok"] and row["hold margin bps"] > 0,
                ),
                self._formula_row(
                    trade, row, "3 acceptance",
                    "acceptance_count >= acceptance_bars",
                    f"acceptance_count={row['accept']:.0f} >= "
                    f"required={row['accept min']:.0f}",
                    row["accept ok"],
                ),
                self._formula_row(
                    trade, row, "4 momentum",
                    "z = drift / scale; |z| >= z_min and sign(z) = side",
                    f"z={row['momentum numerator bps']:+.2f}/"
                    f"{row['momentum scale bps']:.2f}={row['momentum_z']:+.3f}; "
                    f"|z| >= {row['mom min']:.2f}",
                    row["mom ok"],
                ),
                self._formula_row(
                    trade, row, "5 participation",
                    "RVOL = (close * volume) / median_dollar_volume >= rvol_min",
                    f"{row['dollar volume']:,.0f}/{row['rvol median']:,.0f}="
                    f"{row['rvol']:.3f} >= {row['rvol min']:.2f}",
                    row["rvol ok"],
                ),
                self._formula_row(
                    trade, row, "6 VWAP context",
                    "VWAP = sum(typical_price * volume)/sum(volume); sign(C-VWAP)=side",
                    f"C={row['decision close']:.3f}, VWAP={row['session vwap']:.3f}, "
                    f"side={row['vwap side']:+.0f}",
                    row["vwap ok"],
                ),
                self._formula_row(
                    trade, row, "7 initial risk",
                    "stop_bps = min(stop_sigmas*sigma_H, initial_stop_atr*ATR/C*10000)",
                    f"min({row['sigma stop bps']:.1f}, {row['ATR stop bps']:.1f})="
                    f"{row['initial stop bps']:.1f} bps; level={row['initial stop level']:.3f}",
                    "risk",
                ),
                self._formula_row(
                    trade, row, "8 execution",
                    "decision at bar t close; fill at bar t+1 open plus costs",
                    f"C_t={row['decision close']:.3f} -> fill={row['entry reference']:.3f}; "
                    f"directional gap {row['fill gap bps']:+.1f} bps",
                    "filled",
                ),
            ])
        return pd.DataFrame(rows).set_index(["trade", "rule"])

    def trigger_windows(
        self, run: Run, *, bars_before: int = 6, bars_after: int = 1
    ) -> dict[int, pd.DataFrame]:
        """OHLCV and gate state around each original decision bar.

        ``bars_after=1`` includes the next-open fill bar for execution audit, but
        it is labelled explicitly and is never part of a trigger calculation.
        """
        triggers = self.triggers(run)
        if triggers.empty:
            return {}
        index = self._context.index
        bars = self._context.panel.bars(self.symbol)
        indicators = run.result.signals.indicators[self.symbol]
        selected = [
            "watched_level", "zone", "watched_side", "watched_age",
            "acceptance_count", "momentum_z", "relative_volume", "vwap_side",
            "atr", "horizon_sigma_bps", "initial_stop_bps", "candidate",
            "target_weight",
        ]
        windows = {}
        for trade, trigger in triggers.iterrows():
            decision = pd.Timestamp(
                f"{self.day} {trigger['decision']}", tz="America/New_York"
            ).tz_convert("UTC")
            at = index.get_loc(decision)
            start = max(0, at - bars_before)
            stop = min(len(index), at + bars_after + 1)
            stamps = index[start:stop]
            frame = bars.reindex(stamps).join(indicators[selected].reindex(stamps))
            roles = pd.Series("context", index=stamps, dtype=object)
            break_stamp = pd.Timestamp(
                f"{self.day} {trigger['break time']}", tz="America/New_York"
            ).tz_convert("UTC")
            if break_stamp in roles.index:
                roles.loc[break_stamp] = "break"
            roles.loc[decision] = (
                "break + decision" if roles.loc[decision] == "break" else "decision"
            )
            entry_stamp = pd.Timestamp(
                f"{self.day} {trigger['fill']}", tz="America/New_York"
            ).tz_convert("UTC")
            if entry_stamp in roles.index:
                roles.loc[entry_stamp] = "fill (not used by signal)"
            frame.insert(0, "role", roles)
            frame.index = frame.index.tz_convert("America/New_York")
            windows[int(trade)] = frame
        return windows

    @staticmethod
    def _formula_row(trade, trigger, rule, formula, substitution, passed) -> dict:
        return {
            "trade": trade,
            "decision": trigger["decision"],
            "direction": trigger["dir"],
            "rule": rule,
            "formula": formula,
            "actual substitution": substitution,
            "result": passed,
        }

    def sweep(self, parameter: str, values) -> pd.DataFrame:
        """One parameter, several values, the same day — the tuning loop."""
        rows = {}
        for value in values:
            rows[value] = self.summary(self.run(**{parameter: value}))
        table = pd.DataFrame(rows).T
        table.index.name = parameter
        return table
