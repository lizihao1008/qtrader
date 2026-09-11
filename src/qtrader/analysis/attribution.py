"""Where did the money go, and which part of the design is responsible?

A backtest that loses can lose for four structurally different reasons, and they
call for four different fixes:

* **signal** — the entries have no edge. Gross return per trade is not
  distinguishable from zero (or is negative). Nothing downstream can rescue it;
  changing exits or costs only changes how fast it loses.
* **holding period** — there is an edge but the exit keeps missing it. The trades
  spend time deep in profit and give it back, so realised return is a small
  fraction of the favourable excursion.
* **frequency** — there is a small positive edge per trade, but the strategy
  takes so many trades that the fixed friction outweighs it. The fix is turnover,
  not the signal.
* **cost** — the edge per trade is real and would survive at a lower cost
  assumption but not this one. The fix is execution, or a longer horizon.

The distinction matters because the same net loss can come from any of them, and
picking the wrong one wastes a research cycle. The verdict below is arithmetic,
not judgement: everything rests on the identity

    net_total = n_trades * (gross_per_trade - cost_per_trade)

so the question is always which factor puts it below zero.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: |t| below which a per-trade edge is treated as indistinguishable from zero.
EDGE_T_FLOOR = 2.0

#: Realised return below this share of the favourable excursion suggests the
#: exit, not the signal, is what is losing the money.
CAPTURE_FLOOR = 0.35


@dataclass
class Shortfall:
    """The arithmetic behind a run's net result, and the verdict it implies."""

    trades: int
    gross_per_trade_bps: float
    cost_per_trade_bps: float
    net_per_trade_bps: float
    edge_t_stat: float
    capture_ratio: float
    breakeven_hit_rate: float
    hit_rate: float
    verdict: str
    detail: str

    def summary(self) -> str:
        return (
            f"{self.trades} trades · gross {self.gross_per_trade_bps:+.2f} bps "
            f"(t={self.edge_t_stat:+.2f}) · cost {self.cost_per_trade_bps:.2f} bps "
            f"· net {self.net_per_trade_bps:+.2f} bps\n"
            f"  capture {self.capture_ratio:.0%} of favourable excursion · "
            f"hit rate {self.hit_rate:.1%} vs {self.breakeven_hit_rate:.1%} needed\n"
            f"  VERDICT: {self.verdict} — {self.detail}"
        )


def diagnose_shortfall(episodes) -> Shortfall:
    """Attribute a run's result to signal, holding period, frequency or cost."""
    features = episodes.features
    gross = features["gross_return_bps"]
    cost = features["cost_bps"]

    n = len(features)
    # Weight each round trip by the capital it actually risked. A rule that
    # scales a position out produces a half-size slice, and an unweighted mean
    # counts that slice as a full observation — measured at +2.95 bps unweighted
    # against +0.12 weighted on the same run, with total return *falling*. The
    # equity curve feels the weighted number, so the verdict is drawn from it.
    weights = features["notional"].to_numpy(dtype=float) if "notional" in features \
        else np.ones(n)
    usable = np.isfinite(weights) & (weights > 0)
    if not usable.any():
        weights, usable = np.ones(n), np.ones(n, dtype=bool)

    values = gross.to_numpy(dtype=float)
    mean_gross = float(np.average(values[usable], weights=weights[usable]))
    mean_cost = float(np.average(cost.to_numpy(dtype=float)[usable], weights=weights[usable]))

    # Weighted standard error, so the t-statistic is not inflated by counting
    # small slices as full observations.
    share = weights[usable] / weights[usable].sum()
    variance = float(np.average((values[usable] - mean_gross) ** 2, weights=weights[usable]))
    effective = 1.0 / np.sum(share ** 2)          # Kish effective sample size
    t_stat = (
        mean_gross / np.sqrt(variance / effective)
        if effective > 1 and variance > 0 else float("nan")
    )

    favourable = features["mfe_bps"].clip(lower=0.0)
    capture = float(gross.clip(lower=0).sum() / favourable.sum()) if favourable.sum() else 0.0

    n = int(round(effective)) if np.isfinite(effective) else n
    winners = gross[gross > 0]
    losers = gross[gross <= 0]
    payoff = (
        abs(winners.mean() / losers.mean()) if len(winners) and len(losers) and losers.mean()
        else float("nan")
    )
    breakeven = 1.0 / (1.0 + payoff) if np.isfinite(payoff) and payoff > 0 else float("nan")
    hit = float((gross > 0).mean())

    verdict, detail = _verdict(mean_gross, mean_cost, t_stat, capture, n)
    return Shortfall(
        trades=n,
        gross_per_trade_bps=mean_gross,
        cost_per_trade_bps=mean_cost,
        net_per_trade_bps=mean_gross - mean_cost,
        edge_t_stat=t_stat,
        capture_ratio=capture,
        breakeven_hit_rate=breakeven,
        hit_rate=hit,
        verdict=verdict,
        detail=detail,
    )


def _verdict(gross: float, cost: float, t_stat: float, capture: float, n: int) -> tuple[str, str]:
    """Which factor is putting net per trade below zero."""
    if not np.isfinite(t_stat) or abs(t_stat) < EDGE_T_FLOOR:
        if capture < CAPTURE_FLOOR and gross > -cost:
            return (
                "signal (holding period may compound it)",
                f"gross edge is not distinguishable from zero (t={t_stat:+.2f}), and only "
                f"{capture:.0%} of the favourable excursion is realised — the exit is also "
                "leaving money behind, but there is no established edge for it to leave",
            )
        return (
            "signal",
            f"gross per trade is {gross:+.2f} bps at t={t_stat:+.2f} on {n} trades — "
            "indistinguishable from zero, so no exit, filter or cost change can help",
        )
    if gross <= 0:
        return (
            "signal (adverse)",
            f"gross per trade is reliably negative ({gross:+.2f} bps, t={t_stat:+.2f}); "
            "the entries are systematically wrong, so inverting or discarding them is the "
            "only useful response",
        )
    if capture < CAPTURE_FLOOR:
        return (
            "holding period",
            f"a real edge exists (gross {gross:+.2f} bps, t={t_stat:+.2f}) but only "
            f"{capture:.0%} of the favourable excursion survives to the exit — the exit "
            "rule, not the entry, is where the money is going",
        )
    if gross <= cost:
        return (
            "cost / frequency",
            f"the edge is real (gross {gross:+.2f} bps, t={t_stat:+.2f}) but smaller than "
            f"the {cost:.2f} bps it pays to trade. Fewer, longer trades or cheaper execution "
            "would settle it; a better signal is not required",
        )
    return (
        "none — net positive",
        f"gross {gross:+.2f} bps (t={t_stat:+.2f}) exceeds {cost:.2f} bps of cost; "
        "the remaining question is whether it holds out of sample",
    )


def performance_summary(result, episodes) -> pd.DataFrame:
    """Headline statistics, split long/short, all net of costs."""
    trades = result.trades
    rows = {"all": _side_stats(trades, episodes.features)}
    for side in ("LONG", "SHORT"):
        mask = trades["direction"] == side
        rows[side.lower()] = _side_stats(
            trades.loc[mask], episodes.features.loc[episodes.features["direction"] == side]
        )

    table = pd.DataFrame(rows).T
    table.loc["all", "sharpe"] = result.metrics.get("sharpe", float("nan"))
    table.loc["all", "max_drawdown"] = result.metrics.get("max_drawdown", float("nan"))
    table.loc["all", "daily_turnover"] = result.metrics.get("daily_turnover", float("nan"))
    return table


def _weighted_bps(trades: pd.DataFrame, column: str) -> float:
    """Per-trade return weighted by the capital actually at risk in each trade.

    The unweighted mean treats a half-size scale-out slice as one observation
    equal to a full position, so any rule that splits a position inflates it —
    measured at +2.95 bps unweighted against +0.12 weighted on the same run.
    The equity curve feels the weighted number, so that is the one a verdict
    may be drawn from.
    """
    notional = (trades["shares"].abs() * trades["entry_reference"]).to_numpy(dtype=float)
    values = (trades[column] / (trades["shares"].abs() * trades["entry_reference"])).to_numpy()
    usable = np.isfinite(notional) & np.isfinite(values) & (notional > 0)
    if not usable.any():
        return float("nan")
    return float(np.average(values[usable], weights=notional[usable]) * 1e4)


def _side_stats(trades: pd.DataFrame, features: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0}
    wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"]
    losses = trades.loc[trades["net_pnl"] < 0, "net_pnl"]
    gross_loss = abs(losses.sum())
    return {
        "trades": len(trades),
        "hit_rate": float((trades["net_pnl"] > 0).mean()),
        "expectancy_bps": float(features["net_return_bps"].mean()) if len(features) else np.nan,
        "gross_bps": float(features["gross_return_bps"].mean()) if len(features) else np.nan,
        "gross_bps_weighted": _weighted_bps(trades, "gross_pnl"),
        "net_bps_weighted": _weighted_bps(trades, "net_pnl"),
        "cost_bps": float(features["cost_bps"].mean()) if len(features) else np.nan,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else np.inf,
        "net_pnl": float(trades["net_pnl"].sum()),
        "mean_hold_bars": float(features["hold_bars"].mean()) if len(features) else np.nan,
    }
