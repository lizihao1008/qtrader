import type { Bar } from "./types";
import type { UTCTimestamp } from "lightweight-charts";

export type OverlayId = "ma20" | "ma60" | "ema12" | "ema26" | "bb20";

export const OVERLAYS: { id: OverlayId; label: string }[] = [
  { id: "ma20", label: "MA20" },
  { id: "ma60", label: "MA60" },
  { id: "ema12", label: "EMA12" },
  { id: "ema26", label: "EMA26" },
  { id: "bb20", label: "BOLL(20)" },
];

export type LinePoint = { time: UTCTimestamp; value: number };
export type HistPoint = { time: UTCTimestamp; value: number; color: string };

function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const out: number[] = [];
  for (let i = 0; i < values.length; i++) {
    out.push(i === 0 ? values[0] : values[i] * k + out[i - 1] * (1 - k));
  }
  return out;
}

function stdev(values: number[], period: number): (number | null)[] {
  const mean = sma(values, period);
  const out: (number | null)[] = Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const m = mean[i];
    if (m == null) continue;
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = values[j] - m;
      sum += d * d;
    }
    out[i] = Math.sqrt(sum / period);
  }
  return out;
}

function line(bars: Bar[], values: (number | null)[]): LinePoint[] {
  const points: LinePoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    const value = values[i];
    if (value == null || !Number.isFinite(value)) continue;
    points.push({ time: bars[i].time as UTCTimestamp, value });
  }
  return points;
}

export function overlaySeries(bars: Bar[]) {
  const close = bars.map((bar) => bar.close);
  const ma20 = sma(close, 20);
  const ma60 = sma(close, 60);
  const ema12 = ema(close, 12);
  const ema26 = ema(close, 26);
  const mid = ma20;
  const sd = stdev(close, 20);
  const upper = mid.map((value, i) => (value == null || sd[i] == null ? null : value + 2 * sd[i]!));
  const lower = mid.map((value, i) => (value == null || sd[i] == null ? null : value - 2 * sd[i]!));
  return {
    ma20: line(bars, ma20),
    ma60: line(bars, ma60),
    ema12: line(bars, ema12),
    ema26: line(bars, ema26),
    bbMid: line(bars, mid),
    bbUpper: line(bars, upper),
    bbLower: line(bars, lower),
  };
}

export function macdSeries(bars: Bar[]): { macd: LinePoint[]; signal: LinePoint[]; hist: HistPoint[] } {
  if (bars.length === 0) return { macd: [], signal: [], hist: [] };
  const close = bars.map((bar) => bar.close);
  const fast = ema(close, 12);
  const slow = ema(close, 26);
  const dif = fast.map((value, i) => value - slow[i]);
  const dea = ema(dif, 9);
  const macd: LinePoint[] = [];
  const signal: LinePoint[] = [];
  const hist: HistPoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (i < 33) continue;
    const time = bars[i].time as UTCTimestamp;
    const macdValue = dif[i];
    const signalValue = dea[i];
    const histValue = macdValue - signalValue;
    macd.push({ time, value: macdValue });
    signal.push({ time, value: signalValue });
    hist.push({
      time,
      value: histValue,
      color: histValue >= 0 ? "#26a69a" : "#ef5350",
    });
  }
  return { macd, signal, hist };
}
