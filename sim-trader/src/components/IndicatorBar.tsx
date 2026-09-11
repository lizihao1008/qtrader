import { OVERLAYS, type OverlayId } from "../indicators";

type Props = {
  overlays: OverlayId[];
  onToggle: (id: OverlayId) => void;
};

export default function IndicatorBar({ overlays, onToggle }: Props) {
  const enabled = new Set(overlays);
  return (
    <div className="indicator-bar">
      <span className="indicator-label">Overlays</span>
      {OVERLAYS.map((item) => (
        <label key={item.id} className={enabled.has(item.id) ? "on" : ""}>
          <input
            type="checkbox"
            checked={enabled.has(item.id)}
            onChange={() => onToggle(item.id)}
          />
          {item.label}
        </label>
      ))}
      <span className="macd-tag">MACD(12,26,9)</span>
    </div>
  );
}
