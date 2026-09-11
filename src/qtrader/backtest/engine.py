"""Bar-by-bar portfolio backtest engine.

Timing model (the project's central anti-lookahead invariant)
-------------------------------------------------------------
::

    bar t-1 close   strategy sees this bar and picks target_weights[t-1]
    bar t   open    engine executes those weights at the open, paying costs
    bar t   close   the portfolio is marked to market

A signal computed from bar ``t-1`` can therefore never be filled at a price from
bar ``t-1``. ``execution_lag_bars`` makes the delay explicit; it must stay >= 1
and :class:`ExecutionConfig` refuses to be constructed otherwise.

Position sizing uses equity marked at the *previous* bar's close, which is also
information available before the fill.

Two rules keep simulated turnover honest:

* the engine trades a symbol **only when its target weight changes** — it never
  re-sizes a held position as equity drifts, which with whole-share rounding
  would emit a one-share order almost every bar;
* it **cannot open or increase** exposure unless two separate things hold: the
  execution bar actually printed (there is no fill without a trade — this is a
  property of the fill bar, not of the decision bar), and the symbol was
  eligible when the decision was taken. The second is the strategy's own gate
  repeated, so that a strategy bug cannot open a position the universe layer
  ruled out. Closing such a position is still allowed, since the
  alternative — holding it forever — is worse; those exits are optimistic and
  are counted in ``metrics['stale_exits']``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.panel import BarPanel
from ..data.sessions import session_date
from ..strategies.base import MarketContext, StrategySignals
from .costs import CostModel
from .metrics import compute_metrics
from .portfolio import Portfolio


@dataclass(frozen=True)
class ExecutionConfig:
    """How target weights become orders."""

    initial_cash: float = 100_000.0

    #: Fraction of equity deployed when the strategy asks for ``sum(|w|) == 1``.
    gross_leverage: float = 0.95

    #: Bars between the signal and its fill. Must be >= 1.
    execution_lag_bars: int = 1

    #: Panel field used as the pre-cost execution reference price.
    execution_price: str = "open"

    #: Round order sizes down to whole shares (US equities).
    whole_shares: bool = True

    #: Skip rebalances smaller than this notional; 0 disables the filter.
    min_trade_notional: float = 0.0

    def __post_init__(self) -> None:
        if self.execution_lag_bars < 1:
            raise ValueError(
                "execution_lag_bars must be >= 1; executing on the signal bar itself "
                "would use information that is not available at fill time"
            )
        if self.execution_price not in ("open", "close", "high", "low"):
            raise ValueError(f"unsupported execution_price {self.execution_price!r}")


@dataclass
class BacktestResult:
    """Everything a run produced, ready for metrics, charts and persistence."""

    name: str
    panel: BarPanel
    signals: StrategySignals
    benchmark: str
    equity_curve: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)

    @property
    def traded_symbols(self) -> list[str]:
        """Symbols that actually received a fill, most-traded first."""
        if self.fills.empty:
            return []
        return list(self.fills["symbol"].value_counts().index)


class BacktestEngine:
    """Simulate one strategy over one market context."""

    def __init__(self, cost_model: CostModel | None = None, config: ExecutionConfig | None = None):
        self.cost_model = cost_model or CostModel()
        self.config = config or ExecutionConfig()

    def run(self, context: MarketContext, signals: StrategySignals) -> BacktestResult:
        cfg = self.config
        panel = context.panel
        symbols = list(context.symbols)

        weights = self._aligned_weights(signals, context)

        # The delay between decision and execution. A pending target is a day
        # order: it must not jump an overnight/session gap merely because the
        # next available row belongs to the next trading day (notably after an
        # early close, where a configured wall-clock flatten bar may not exist).
        delayed = weights.shift(cfg.execution_lag_bars).fillna(0.0)
        sessions = pd.Series(session_date(panel.index).to_numpy(), index=panel.index)
        decision_session = sessions.shift(cfg.execution_lag_bars)
        crossed_session = decision_session.isna() | decision_session.ne(sessions)
        delayed.loc[crossed_session] = 0.0

        weight_matrix = delayed.to_numpy(dtype=float)
        reference = panel.field(cfg.execution_price)[symbols].to_numpy(dtype=float)
        # Positions are marked at the last known close, so a symbol that stops
        # printing keeps a value instead of silently dropping out of equity.
        close = panel.close[symbols].ffill().to_numpy(dtype=float)
        # Whether a fill is *possible* is a property of the execution bar, not
        # of the decision bar: an order resting during a bar in which nothing
        # traded simply does not get filled. That is `traded`, and it is not
        # shifted. The liquidity mask (`context.tradable`) answers a different
        # question — is this symbol worth trading at all — and is the strategy's
        # concern, applied at the bar it decides on. Using the liquidity mask
        # here let fills through on bars with no print, because it tolerates up
        # to `max_stale_bars` of silence.
        printed = panel.traded[symbols].to_numpy(dtype=bool)
        # Eligibility is a separate question and stays a guard of its own: the
        # strategy already applies it, and the engine repeating it means a
        # strategy bug cannot open a position in a symbol the universe layer
        # ruled out.
        eligible = (
            context.tradable[symbols]
            .shift(cfg.execution_lag_bars)
            .fillna(False)
        )
        eligible.loc[crossed_session] = False
        eligible = eligible.to_numpy(dtype=bool)

        portfolio = Portfolio(cfg.initial_cash, self.cost_model)
        equity_at_last_close = cfg.initial_cash
        applied_weight = np.zeros(len(symbols))
        held = np.zeros(len(symbols))
        stale_exits = 0
        records = []

        for i, timestamp in enumerate(panel.index):
            wanted = weight_matrix[i]
            for j in np.nonzero(wanted != applied_weight)[0]:
                price = reference[i, j]
                if not np.isfinite(price) or price <= 0:
                    continue  # no price yet today: nothing can be executed

                target = self._target_shares(
                    weight=wanted[j], equity=equity_at_last_close, price=price
                )
                increases = abs(target) > abs(held[j]) or (target * held[j] < 0)
                if not (printed[i, j] and eligible[i, j]):
                    if increases:
                        continue  # fail closed; retry on a bar where it prints
                    stale_exits += 1
                if abs(target - held[j]) * price < cfg.min_trade_notional:
                    applied_weight[j] = wanted[j]  # too small to be worth trading
                    continue

                portfolio.rebalance_to(
                    symbols[j], target, timestamp=timestamp, reference_price=price
                )
                held[j] = target
                applied_weight[j] = wanted[j]

            marks = close[i]
            equity_at_last_close = portfolio.cash + float(np.nansum(held * marks))
            exposure = held * marks
            records.append(
                {
                    "timestamp": timestamp,
                    "cash": portfolio.cash,
                    "equity": equity_at_last_close,
                    "gross_exposure": float(np.nansum(np.abs(exposure))),
                    "net_exposure": float(np.nansum(exposure)),
                    "n_positions": int((held != 0).sum()),
                }
            )

        equity_curve = self._build_equity_curve(
            records, panel, cfg.initial_cash, benchmark=context.universe.benchmark
        )
        trades = portfolio.trades_frame()
        fills = portfolio.fills_frame()
        metrics = compute_metrics(equity_curve, trades, fills)
        metrics["stale_exits"] = stale_exits

        return BacktestResult(
            name=context.universe.name,
            panel=panel,
            signals=signals,
            benchmark=context.universe.benchmark,
            equity_curve=equity_curve,
            fills=fills,
            trades=trades,
            metrics=metrics,
        )

    # ------------------------------------------------------------- internals
    def _aligned_weights(self, signals: StrategySignals, context: MarketContext) -> pd.DataFrame:
        """Weights reindexed onto the context's bars and tradable symbols."""
        weights = signals.target_weights
        extra = set(weights.columns) - set(context.symbols)
        if extra:
            raise ValueError(f"strategy asked for untradable symbols: {sorted(extra)}")
        return weights.reindex(index=context.index, columns=list(context.symbols)).fillna(0.0)

    def _target_shares(self, *, weight: float, equity: float, price: float) -> float:
        """Convert a target weight into a share count."""
        if weight == 0:
            return 0.0
        shares = equity * self.config.gross_leverage * weight / price
        if self.config.whole_shares:
            shares = math.floor(abs(shares)) * (1 if shares > 0 else -1)
        return float(shares)

    def _build_equity_curve(
        self, records: list[dict], panel: BarPanel, initial_cash: float, *, benchmark: str
    ) -> pd.DataFrame:
        curve = pd.DataFrame(records).set_index("timestamp")
        curve["return"] = curve["equity"].pct_change().fillna(0.0)
        curve["cum_return"] = curve["equity"] / initial_cash - 1.0
        curve["drawdown"] = curve["equity"] / curve["equity"].cummax() - 1.0

        # Frictionless buy-and-hold of the benchmark, for context only.
        prices = panel.close[benchmark].ffill()
        curve["benchmark_cum_return"] = prices / prices.dropna().iloc[0] - 1.0
        return curve
