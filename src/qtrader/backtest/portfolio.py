"""Portfolio accounting across symbols.

Owns cash, per-symbol share counts, fills and round-trip trades. It knows
nothing about time ordering or signals — the engine feeds it one rebalance at a
time — which keeps position and PnL arithmetic testable in isolation.

Conventions
-----------
* positive shares = long, negative = short;
* ``cash`` is decreased by purchases and increased by sales, so a short sale
  credits cash and equity stays ``cash + sum(shares * mark_price)``;
* a **trade** is one round trip in one symbol: from flat (or from a reversal)
  back to flat;
* PnL is decomposed so execution quality stays visible::

      gross_pnl = direction * (exit_reference - entry_reference) * shares
      costs     = commission + spread/slippage paid on both legs
      net_pnl   = gross_pnl - costs        (equals the realised cash change)

  Short positions carry no borrow cost or locate constraint yet; short results
  are optimistic until that is modelled.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .costs import BUY, SELL, CostModel


@dataclass(frozen=True)
class Fill:
    """One executed order."""

    timestamp: pd.Timestamp
    symbol: str
    side: int  # +1 buy, -1 sell
    shares: float  # signed change in position
    price: float  # cost-adjusted execution price
    reference_price: float  # bar price before costs
    commission: float

    @property
    def slippage_cost(self) -> float:
        """Cash lost to spread + slippage on this fill."""
        return abs(self.shares) * abs(self.price - self.reference_price)


@dataclass(frozen=True)
class Trade:
    """One completed round trip in one symbol."""

    symbol: str
    direction: int  # +1 long, -1 short
    shares: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float  # volume-weighted fill price actually paid
    exit_price: float
    entry_reference: float  # bar price before costs, i.e. the frictionless fill
    exit_reference: float
    commission: float
    slippage_cost: float

    @property
    def gross_pnl(self) -> float:
        return self.direction * (self.exit_reference - self.entry_reference) * self.shares

    @property
    def costs(self) -> float:
        return self.commission + self.slippage_cost

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.costs

    @property
    def return_pct(self) -> float:
        """Net return on the traded notional."""
        notional = abs(self.shares) * self.entry_reference
        return self.net_pnl / notional if notional else 0.0

    @property
    def holding_bars(self) -> pd.Timedelta:
        return self.exit_time - self.entry_time


@dataclass
class _OpenLot:
    """Exposure opened but not yet closed, with its accumulated entry costs."""

    direction: int
    shares: float
    entry_time: pd.Timestamp
    entry_price: float
    entry_reference: float
    commission: float
    slippage_cost: float


class Portfolio:
    """Cash and positions across any number of symbols."""

    def __init__(self, initial_cash: float, cost_model: CostModel):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.cost_model = cost_model
        self.positions: dict[str, float] = {}
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self._lots: dict[str, _OpenLot] = {}

    # ---------------------------------------------------------------- state
    def shares(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def equity(self, mark_prices: dict[str, float] | pd.Series) -> float:
        """Mark-to-market account value."""
        holdings = sum(
            shares * mark_prices[symbol]
            for symbol, shares in self.positions.items()
            if shares
        )
        return self.cash + holdings

    # --------------------------------------------------------------- trading
    def rebalance_to(
        self,
        symbol: str,
        target_shares: float,
        *,
        timestamp: pd.Timestamp,
        reference_price: float,
    ) -> Fill | None:
        """Trade the difference between the current and target share count."""
        delta = target_shares - self.shares(symbol)
        if delta == 0:
            return None

        side = BUY if delta > 0 else SELL
        price = self.cost_model.fill_price(reference_price, side)
        notional = delta * price
        commission = self.cost_model.commission(delta, notional)

        self.cash -= notional + commission
        self.positions[symbol] = target_shares

        fill = Fill(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            shares=delta,
            price=price,
            reference_price=reference_price,
            commission=commission,
        )
        self.fills.append(fill)
        self._record_trade(fill)
        return fill

    # ------------------------------------------------------- trade bookkeeping
    def _record_trade(self, fill: Fill) -> None:
        """Fold a fill into the symbol's open lot, emitting a Trade when it closes."""
        lot = self._lots.get(fill.symbol)
        if lot is None:
            self._lots[fill.symbol] = self._open_lot(fill, shares=abs(fill.shares), share=1.0)
            return

        filled = abs(fill.shares)
        if (fill.shares > 0) == (lot.direction > 0):
            self._add_to_lot(lot, fill, filled)
            return

        # Reducing or reversing: the overlapping shares complete a round trip.
        closing = min(lot.shares, filled)
        entry_share = closing / lot.shares
        exit_share = closing / filled

        self.trades.append(
            Trade(
                symbol=fill.symbol,
                direction=lot.direction,
                shares=closing,
                entry_time=lot.entry_time,
                exit_time=fill.timestamp,
                entry_price=lot.entry_price,
                exit_price=fill.price,
                entry_reference=lot.entry_reference,
                exit_reference=fill.reference_price,
                commission=lot.commission * entry_share + fill.commission * exit_share,
                slippage_cost=lot.slippage_cost * entry_share + fill.slippage_cost * exit_share,
            )
        )

        remaining_open = lot.shares - closing
        remaining_fill = filled - closing
        if remaining_open > 0:
            keep = remaining_open / lot.shares
            lot.shares = remaining_open
            lot.commission *= keep
            lot.slippage_cost *= keep
        elif remaining_fill > 0:
            self._lots[fill.symbol] = self._open_lot(
                fill, shares=remaining_fill, share=remaining_fill / filled
            )
        else:
            del self._lots[fill.symbol]

    @staticmethod
    def _open_lot(fill: Fill, *, shares: float, share: float) -> _OpenLot:
        """Start a lot from ``shares`` of ``fill``, carrying that share of its costs."""
        return _OpenLot(
            direction=1 if fill.shares > 0 else -1,
            shares=shares,
            entry_time=fill.timestamp,
            entry_price=fill.price,
            entry_reference=fill.reference_price,
            commission=fill.commission * share,
            slippage_cost=fill.slippage_cost * share,
        )

    @staticmethod
    def _add_to_lot(lot: _OpenLot, fill: Fill, filled: float) -> None:
        """Scale the lot up, volume-weighting both fill and reference prices."""
        total = lot.shares + filled
        lot.entry_price = (lot.entry_price * lot.shares + fill.price * filled) / total
        lot.entry_reference = (
            lot.entry_reference * lot.shares + fill.reference_price * filled
        ) / total
        lot.shares = total
        lot.commission += fill.commission
        lot.slippage_cost += fill.slippage_cost

    # ---------------------------------------------------------------- frames
    def fills_frame(self) -> pd.DataFrame:
        columns = [
            "timestamp", "symbol", "side", "shares", "price", "reference_price",
            "commission", "slippage_cost",
        ]
        rows = [
            {
                "timestamp": f.timestamp,
                "symbol": f.symbol,
                "side": "BUY" if f.side == BUY else "SELL",
                "shares": f.shares,
                "price": f.price,
                "reference_price": f.reference_price,
                "commission": f.commission,
                "slippage_cost": f.slippage_cost,
            }
            for f in self.fills
        ]
        return pd.DataFrame(rows, columns=columns)

    def trades_frame(self) -> pd.DataFrame:
        columns = [
            "symbol", "entry_time", "exit_time", "direction", "shares",
            "entry_price", "exit_price", "entry_reference", "exit_reference",
            "gross_pnl", "commission", "slippage_cost", "costs", "net_pnl", "return_pct",
        ]
        rows = [
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "direction": "LONG" if t.direction > 0 else "SHORT",
                "shares": t.shares,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "entry_reference": t.entry_reference,
                "exit_reference": t.exit_reference,
                "gross_pnl": t.gross_pnl,
                "commission": t.commission,
                "slippage_cost": t.slippage_cost,
                "costs": t.costs,
                "net_pnl": t.net_pnl,
                "return_pct": t.return_pct,
            }
            for t in self.trades
        ]
        return pd.DataFrame(rows, columns=columns)
