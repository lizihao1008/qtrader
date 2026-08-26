"""Transaction-cost model.

A backtest here is never frictionless (CLAUDE.md §11.4). Every fill pays:

* **half spread** — we cross the book, so a buy lifts the offer and a sell hits
  the bid;
* **slippage** — additional adverse move between decision and fill;
* **commission** — per-share and/or notional-based, with an optional minimum.

Spread and slippage are expressed in basis points of the reference price, which
keeps the model scale-free across symbols. All three components are charged
against the strategy, never against the benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass

BPS = 1e-4

BUY, SELL = 1, -1


@dataclass(frozen=True)
class CostModel:
    """Cost assumptions for one backtest run."""

    half_spread_bps: float = 1.0
    slippage_bps: float = 0.5
    commission_bps: float = 0.0
    commission_per_share: float = 0.0
    min_commission: float = 0.0

    @property
    def impact_bps(self) -> float:
        """Total adverse price move applied to every fill."""
        return self.half_spread_bps + self.slippage_bps

    def fill_price(self, reference_price: float, side: int) -> float:
        """Price actually paid/received when trading ``side`` (+1 buy, -1 sell)."""
        if side not in (BUY, SELL):
            raise ValueError(f"side must be +1 (buy) or -1 (sell), got {side}")
        return reference_price * (1.0 + side * self.impact_bps * BPS)

    def commission(self, shares: float, notional: float) -> float:
        """Broker fee for a trade of ``shares`` shares worth ``notional``."""
        if shares == 0:
            return 0.0
        fee = abs(shares) * self.commission_per_share + abs(notional) * self.commission_bps * BPS
        return max(fee, self.min_commission)
