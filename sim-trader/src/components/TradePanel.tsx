import { LEVERAGE, SLIPPAGE_BPS, exposureFromMargin, formatMoney, slippedPrice } from "../engine";
import type { Position } from "../types";

type Props = {
  amount: number;
  price: number | null;
  cash: number;
  position: Position | null;
  pnl: number;
  canTrade: boolean;
  onAmount: (amount: number) => void;
  onLong: () => void;
  onShort: () => void;
  onReduce: () => void;
  onClose: () => void;
};

const SIZE_PCTS = [0.25, 0.5, 0.75, 1] as const;

function marginFromPct(cash: number, pct: number): number {
  return Math.round(cash * pct * 100) / 100;
}

export default function TradePanel(props: Props) {
  const exposure = exposureFromMargin(props.amount);
  const previewFill = props.price == null ? null : slippedPrice(props.price, "long", "open");
  const shares = previewFill && previewFill > 0 ? exposure / previewFill : 0;
  const inPosition = !!props.position;
  const reduceBy = props.position ? Math.min(props.amount, props.position.margin) : 0;
  return (
    <aside className="trade-panel">
      <h3>Order · {LEVERAGE}x</h3>
      <div className="qty">
        <label>{inPosition ? "Add / reduce margin" : "Amount (margin)"}</label>
        <input
          type="number"
          min={1}
          step={50}
          value={props.amount}
          onChange={(e) => props.onAmount(Math.max(0, Number(e.target.value) || 0))}
        />
      </div>
      <div className="size-row">
        {SIZE_PCTS.map((pct) => {
          const sized = marginFromPct(props.cash, pct);
          const active = props.cash > 0 && Math.abs(props.amount - sized) < 0.005;
          return (
            <button
              key={pct}
              type="button"
              className={active ? "size-btn active" : "size-btn"}
              disabled={props.cash <= 0}
              onClick={() => props.onAmount(sized)}
            >
              {pct * 100}%
            </button>
          );
        })}
      </div>
      <button
        className="order long"
        disabled={!props.canTrade || (inPosition && props.position?.side !== "long")}
        onClick={props.onLong}
      >
        {props.position?.side === "long" ? `Add ${formatMoney(props.amount)}` : "Long"}
      </button>
      <button
        className="order short"
        disabled={!props.canTrade || (inPosition && props.position?.side !== "short")}
        onClick={props.onShort}
      >
        {props.position?.side === "short" ? `Add ${formatMoney(props.amount)}` : "Short"}
      </button>
      <button
        className="order reduce"
        disabled={!props.position || !props.canTrade || props.amount <= 0}
        onClick={props.onReduce}
      >
        Reduce {reduceBy > 0 ? formatMoney(reduceBy) : ""}
      </button>
      <button className="order close" disabled={!props.position} onClick={props.onClose}>
        Close position
      </button>
      <div className="panel-kv">
        <div>
          Leverage
          <b>{LEVERAGE}x</b>
        </div>
        <div>
          {inPosition ? "Adj. exposure" : "Exposure"}
          <b>{formatMoney(exposure)}</b>
        </div>
        <div>
          Est. shares
          <b>{shares > 0 ? shares.toFixed(4) : "—"}</b>
        </div>
        <div>
          Used margin
          <b>{props.position ? formatMoney(props.position.margin) : "—"}</b>
        </div>
        <div>
          Slippage
          <b>{SLIPPAGE_BPS.toFixed(1)} bps / fill</b>
        </div>
        <div>
          Free cash
          <b>{formatMoney(props.cash)}</b>
        </div>
        <div>
          Current price
          <b>{props.price == null ? "—" : formatMoney(props.price)}</b>
        </div>
        <div>
          Avg open
          <b>{props.position ? formatMoney(props.position.entry) : "—"}</b>
        </div>
        <div>
          Floating P&L
          <b className={props.pnl >= 0 ? "up" : "down"}>{formatMoney(props.pnl)}</b>
        </div>
      </div>
    </aside>
  );
}
