import type { Bar, Lot, Position, Side } from "./types";

export const INITIAL_CASH = 10_000;
export const LEVERAGE = 20;
export const SLIPPAGE_BPS = 1.5;
const BASE_MS = 500;

export function intervalMs(speed: number): number {
  return Math.max(40, BASE_MS / speed);
}

export function slippedPrice(mid: number, side: Side, action: "open" | "close"): number {
  const slip = SLIPPAGE_BPS / 1e4;
  const buying =
    (action === "open" && side === "long") || (action === "close" && side === "short");
  return buying ? mid * (1 + slip) : mid * (1 - slip);
}

export function exposureFromMargin(margin: number): number {
  return margin * LEVERAGE;
}

export function lotPnl(side: Side, entry: number, qty: number, fill: number): number {
  return side === "long" ? (fill - entry) * qty : (entry - fill) * qty;
}

export function positionFromLots(side: Side, lots: Lot[], margin: number, openedAt: string): Position {
  const qty = lots.reduce((sum, lot) => sum + lot.qty, 0);
  const cost = lots.reduce((sum, lot) => sum + lot.entry * lot.qty, 0);
  return {
    side,
    lots,
    qty,
    entry: qty > 0 ? cost / qty : 0,
    margin,
    notional: exposureFromMargin(margin),
    openedAt,
  };
}

export function openPosition(
  side: Side,
  amount: number,
  mid: number,
  openedAt: string,
  time: number,
): Position | { error: string } {
  if (amount <= 0) return { error: "Enter a margin amount." };
  const fill = slippedPrice(mid, side, "open");
  const notional = exposureFromMargin(amount);
  const qty = notional / fill;
  if (!Number.isFinite(qty) || qty <= 0) return { error: "Invalid order size." };
  return positionFromLots(side, [{ qty, entry: fill, openedAt, time }], amount, openedAt);
}

function lotsOf(position: Position): Lot[] {
  if (position.lots?.length) return position.lots;
  return [{ qty: position.qty, entry: position.entry, openedAt: position.openedAt, time: 0 }];
}

function peelLots(
  lots: Lot[],
  qtyToClose: number,
  side: Side,
  fill: number,
): { remaining: Lot[]; closedQty: number; closedEntry: number; pnl: number } {
  let left = qtyToClose;
  const remaining = [...lots];
  let closedQty = 0;
  let closedCost = 0;
  let pnl = 0;
  while (left > 1e-12 && remaining.length > 0) {
    const lot = remaining.pop()!;
    const take = Math.min(lot.qty, left);
    pnl += lotPnl(side, lot.entry, take, fill);
    closedQty += take;
    closedCost += lot.entry * take;
    left -= take;
    if (lot.qty - take > 1e-12) {
      remaining.push({ ...lot, qty: lot.qty - take });
    }
  }
  return {
    remaining,
    closedQty,
    closedEntry: closedQty > 0 ? closedCost / closedQty : 0,
    pnl,
  };
}

export function unrealizedPnl(position: Position | null, price: number): number {
  if (!position) return 0;
  return lotsOf(position).reduce((sum, lot) => sum + lotPnl(position.side, lot.entry, lot.qty, price), 0);
}

export function equity(cash: number, position: Position | null, price: number): number {
  if (!position) return cash;
  return cash + position.margin + unrealizedPnl(position, price);
}

export function addMargin(
  cash: number,
  position: Position,
  amount: number,
  mid: number,
  openedAt: string,
  time: number,
): { cash: number; position: Position } | { error: string } {
  if (amount <= 0) return { error: "Enter a margin amount." };
  if (amount > cash) return { error: "Not enough free cash to add this margin." };
  const fill = slippedPrice(mid, position.side, "open");
  const extraNotional = exposureFromMargin(amount);
  const extraQty = extraNotional / fill;
  if (!Number.isFinite(extraQty) || extraQty <= 0) return { error: "Invalid order size." };
  return {
    cash: cash - amount,
    position: positionFromLots(
      position.side,
      [...lotsOf(position), { qty: extraQty, entry: fill, openedAt, time }],
      position.margin + amount,
      position.openedAt,
    ),
  };
}

export function reduceMargin(
  cash: number,
  position: Position,
  amount: number,
  mid: number,
):
  | {
      cash: number;
      position: Position | null;
      closedQty: number;
      closedMargin: number;
      closedEntry: number;
      fill: number;
      pnl: number;
    }
  | { error: string } {
  if (amount <= 0) return { error: "Enter a margin amount." };
  const closedMargin = Math.min(amount, position.margin);
  const fill = slippedPrice(mid, position.side, "close");
  const qtyAtNow = exposureFromMargin(closedMargin) / fill;
  const closeAll =
    closedMargin >= position.margin - 1e-9 || qtyAtNow >= position.qty - 1e-9;
  const peeled = peelLots(lotsOf(position), closeAll ? position.qty : qtyAtNow, position.side, fill);
  const released = closeAll ? position.margin : closedMargin;
  return {
    cash: cash + released + peeled.pnl,
    position:
      closeAll || peeled.remaining.length === 0
        ? null
        : positionFromLots(position.side, peeled.remaining, position.margin - released, position.openedAt),
    closedQty: peeled.closedQty,
    closedMargin: released,
    closedEntry: peeled.closedEntry,
    fill,
    pnl: peeled.pnl,
  };
}

export function formatMoney(value: number): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

export function visibleBars(bars: Bar[], cursor: number): Bar[] {
  return bars.slice(0, Math.max(0, cursor));
}
