export type Side = "long" | "short";

export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  clock: string;
};

export type Lot = {
  qty: number;
  entry: number;
  openedAt: string;
  time: number;
};

export type Position = {
  side: Side;
  qty: number;
  entry: number;
  margin: number;
  notional: number;
  openedAt: string;
  lots: Lot[];
};

export type ClosedTrade = {
  id: number;
  openedAt: string;
  closedAt: string;
  side: Side;
  qty: number;
  margin: number;
  entry: number;
  exit: number;
  pnl: number;
};
