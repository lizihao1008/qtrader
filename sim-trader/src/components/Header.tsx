import { formatMoney, formatPct } from "../engine";
import type { Position } from "../types";

type Props = {
  symbols: string[];
  symbol: string;
  days: string[];
  day: string;
  price: number | null;
  clock: string;
  cash: number;
  equity: number;
  initial: number;
  position: Position | null;
  onSymbol: (symbol: string) => void;
  onDay: (day: string) => void;
  onReset: () => void;
};

export default function Header(props: Props) {
  const change = props.equity - props.initial;
  const ret = props.initial === 0 ? 0 : change / props.initial;
  return (
    <header className="header">
      <div className="brand">
        SIM<span>TRADER</span>
      </div>
      <div className="instrument">
        <select value={props.symbol} onChange={(e) => props.onSymbol(e.target.value)}>
          {props.symbols.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <select value={props.day} onChange={(e) => props.onDay(e.target.value)}>
          {props.days.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>
      <div className="price-block">
        <div className="px">{props.price == null ? "—" : formatMoney(props.price)}</div>
        <div className="clock">{props.clock || "Waiting for open"} · ET</div>
      </div>
      <div className="stats">
        <div className="stat">
          <label>Leverage</label>
          <b>20x</b>
        </div>
        <div className="stat">
          <label>Free cash</label>
          <b>{formatMoney(props.cash)}</b>
        </div>
        <div className="stat">
          <label>Equity</label>
          <b>{formatMoney(props.equity)}</b>
        </div>
        <div className="stat">
          <label>P&L</label>
          <b className={change >= 0 ? "up" : "down"}>
            {formatMoney(change)} ({formatPct(ret)})
          </b>
        </div>
        <div className="stat">
          <label>Position</label>
          <b>
            {props.position
              ? `${props.position.side.toUpperCase()} ${formatMoney(props.position.notional)}`
              : "FLAT"}
          </b>
        </div>
        <button type="button" className="account-reset" onClick={props.onReset}>
          Reset
        </button>
      </div>
    </header>
  );
}
