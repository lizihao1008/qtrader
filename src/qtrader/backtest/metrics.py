"""Performance metrics for a completed run.

Annualisation uses **trading time, not wall-clock time**: intraday bars are
regular-hours only, so scaling by calendar days would inflate every ratio. The
engine's bar frame is converted to an implied bars-per-year using the observed
number of bars per session and a 252-session year.

All figures are net of costs — the equity curve the engine produced already
paid spread, slippage and commission.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.sessions import session_date

TRADING_DAYS_PER_YEAR = 252


def bars_per_year(index: pd.DatetimeIndex) -> float:
    """Implied number of bars in a trading year for this sampling frequency."""
    n_sessions = session_date(index).nunique()
    if n_sessions == 0:
        return float(TRADING_DAYS_PER_YEAR)
    return (len(index) / n_sessions) * TRADING_DAYS_PER_YEAR


def compute_metrics(
    equity_curve: pd.DataFrame, trades: pd.DataFrame, fills: pd.DataFrame | None = None
) -> dict:
    """Strategy, risk, trade and turnover statistics for one backtest result."""
    returns = equity_curve["return"]
    equity = equity_curve["equity"]
    periods = bars_per_year(equity_curve.index)
    n_sessions = int(session_date(equity_curve.index).nunique())

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) else 0.0
    years = n_sessions / TRADING_DAYS_PER_YEAR if n_sessions else 0.0
    annualized = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else float("nan")

    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    mean = float(returns.mean()) if len(returns) else 0.0

    max_drawdown = float(equity_curve["drawdown"].min()) if len(equity_curve) else 0.0

    metrics = {
        "start": str(equity_curve.index.min()) if len(equity_curve) else None,
        "end": str(equity_curve.index.max()) if len(equity_curve) else None,
        "n_bars": int(len(equity_curve)),
        "n_sessions": n_sessions,
        "initial_equity": float(equity.iloc[0]) if len(equity) else 0.0,
        "final_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
        "total_return": total_return,
        "benchmark_return": float(equity_curve["benchmark_cum_return"].iloc[-1])
        if len(equity_curve)
        else 0.0,
        "annualized_return": annualized,
        "annualized_volatility": std * np.sqrt(periods),
        "sharpe": (mean / std * np.sqrt(periods)) if std > 0 else float("nan"),
        "sortino": (mean / downside_std * np.sqrt(periods)) if downside_std > 0 else float("nan"),
        "max_drawdown": max_drawdown,
        "calmar": (annualized / abs(max_drawdown)) if max_drawdown < 0 else float("nan"),
        "avg_gross_exposure": float(
            (equity_curve["gross_exposure"] / equity_curve["equity"]).mean()
        )
        if len(equity_curve)
        else 0.0,
        "avg_net_exposure": float(
            (equity_curve["net_exposure"] / equity_curve["equity"]).mean()
        )
        if len(equity_curve)
        else 0.0,
        "time_in_market": float((equity_curve["n_positions"] > 0).mean())
        if len(equity_curve)
        else 0.0,
        "avg_positions": float(equity_curve["n_positions"].mean()) if len(equity_curve) else 0.0,
    }
    metrics.update(_trade_metrics(trades))
    metrics["daily_turnover"] = _daily_turnover(equity_curve, fills, n_sessions)
    return metrics


def _daily_turnover(
    equity_curve: pd.DataFrame, fills: pd.DataFrame | None, n_sessions: int
) -> float:
    """Traded notional per session as a multiple of average equity.

    1.0 means the book is fully turned over once a day. High turnover is not
    wrong in itself, but it is what converts a small per-trade edge into a
    guaranteed cost bill.
    """
    if fills is None or fills.empty or n_sessions == 0:
        return float("nan")
    traded = float((fills["shares"].abs() * fills["reference_price"]).sum())
    average_equity = float(equity_curve["equity"].mean())
    if average_equity <= 0:
        return float("nan")
    return traded / average_equity / n_sessions


def _trade_metrics(trades: pd.DataFrame) -> dict:
    """Round-trip statistics. Empty runs report zeros rather than failing."""
    if trades.empty:
        return {
            "n_trades": 0,
            "hit_rate": float("nan"),
            "profit_factor": float("nan"),
            "avg_win": float("nan"),
            "avg_loss": float("nan"),
            "avg_trade_return": float("nan"),
            "total_costs": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
        }

    wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"]
    losses = trades.loc[trades["net_pnl"] < 0, "net_pnl"]
    gross_loss = float(losses.sum())

    return {
        "n_trades": int(len(trades)),
        "hit_rate": float(len(wins) / len(trades)),
        "profit_factor": float(wins.sum() / abs(gross_loss)) if gross_loss < 0 else float("inf"),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "avg_trade_return": float(trades["return_pct"].mean()),
        "total_costs": float(trades["costs"].sum()),
        "gross_pnl": float(trades["gross_pnl"].sum()),
        "net_pnl": float(trades["net_pnl"].sum()),
    }
