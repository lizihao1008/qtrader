import { formatMoney } from "../engine";
import type { ClosedTrade, Position } from "../types";

type Props = {
  position: Position | null;
  price: number | null;
  pnl: number;
  history: ClosedTrade[];
};

export default function Bottom({ position, price, pnl, history }: Props) {
  return (
    <div className="bottom">
      <section>
        <h3>Open position</h3>
        {position && price != null ? (
          <table>
            <tbody>
              <tr>
                <th>Side</th>
                <td>
                  <span className={`badge ${position.side}`}>{position.side}</span>
                </td>
              </tr>
              <tr>
                <th>Qty</th>
                <td>{position.qty.toFixed(4)}</td>
              </tr>
              <tr>
                <th>Margin</th>
                <td>{formatMoney(position.margin)}</td>
              </tr>
              <tr>
                <th>Exposure</th>
                <td>{formatMoney(position.notional)}</td>
              </tr>
              <tr>
                <th>Avg open</th>
                <td>{formatMoney(position.entry)}</td>
              </tr>
              {(position.lots ?? []).map((lot, index) => (
                <tr key={`${lot.openedAt}-${index}`}>
                  <th>Lot {index + 1}</th>
                  <td>
                    {lot.qty.toFixed(4)} @ {formatMoney(lot.entry)}
                  </td>
                </tr>
              ))}
              <tr>
                <th>Now</th>
                <td>{formatMoney(price)}</td>
              </tr>
              <tr>
                <th>P&L</th>
                <td className={pnl >= 0 ? "up" : "down"}>{formatMoney(pnl)}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <div className="empty">No open position</div>
        )}
      </section>
      <section className="history">
        <h3>Trade history</h3>
        {history.length === 0 ? (
          <div className="empty">No closed trades yet</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Opened</th>
                <th>Closed</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().map((trade) => (
                <tr key={trade.id}>
                  <td>{trade.openedAt}</td>
                  <td>{trade.closedAt}</td>
                  <td>
                    <span className={`badge ${trade.side}`}>{trade.side}</span>
                  </td>
                  <td>{formatMoney(trade.entry)}</td>
                  <td>{formatMoney(trade.exit)}</td>
                  <td className={trade.pnl >= 0 ? "up" : "down"}>{formatMoney(trade.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
