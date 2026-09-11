import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { formatMoney, lotPnl } from "../engine";
import { macdSeries, overlaySeries, type OverlayId } from "../indicators";
import type { Bar, Position } from "../types";

const BAR_SPACING = 8;
const LONG_COLOR = "#00c853";
const SHORT_COLOR = "#ff4d4f";

type OverlaySeriesMap = {
  ma20: ISeriesApi<"Line">;
  ma60: ISeriesApi<"Line">;
  ema12: ISeriesApi<"Line">;
  ema26: ISeriesApi<"Line">;
  bbMid: ISeriesApi<"Line">;
  bbUpper: ISeriesApi<"Line">;
  bbLower: ISeriesApi<"Line">;
};

type Props = {
  bars: Bar[];
  overlays: OverlayId[];
  position: Position | null;
};

function pinFromLeft(chart: IChartApi, host: HTMLElement, count: number) {
  const capacity = Math.max(1, Math.floor(host.clientWidth / BAR_SPACING));
  const scale = chart.timeScale();
  if (count <= capacity) {
    scale.setVisibleLogicalRange({ from: 0, to: capacity });
  } else {
    scale.setVisibleLogicalRange({ from: count - capacity, to: count });
  }
}

function restoreVisibleRange(
  chart: IChartApi,
  previous: { from: number; to: number } | null,
  previousCount: number,
  count: number,
) {
  if (!previous || previousCount <= 0) return;
  const scale = chart.timeScale();
  const lastBar = previousCount - 1;
  const following = previous.to <= lastBar + 2 && previous.to >= lastBar - 1;
  if (following && count !== previousCount) {
    const shift = count - previousCount;
    scale.setVisibleLogicalRange({ from: previous.from + shift, to: previous.to + shift });
    return;
  }
  scale.setVisibleLogicalRange(previous);
}

function formatSignedMoney(value: number): string {
  const text = formatMoney(value);
  return value > 0 ? `+${text}` : text;
}

function lastPrice(bars: Bar[]): number | null {
  return bars.at(-1)?.close ?? null;
}

function entryDecorations(
  position: Position | null,
  price: number | null,
): {
  lines: { price: number; title: string; dashed: boolean }[];
  markers: SeriesMarker<UTCTimestamp>[];
} {
  if (!position) return { lines: [], markers: [] };
  const lots = position.lots ?? [];
  const color = position.side === "long" ? LONG_COLOR : SHORT_COLOR;
  const arrow = position.side === "long" ? "arrowUp" : "arrowDown";
  const barPos = position.side === "long" ? "belowBar" : "aboveBar";
  const totalPnl =
    price == null ? null : lots.reduce((sum, lot) => sum + lotPnl(position.side, lot.entry, lot.qty, price), 0);
  const lines = lots.map((lot, index) => {
    const base = index === 0 ? "Open" : `Add ${index}`;
    const title =
      index === 0 && totalPnl != null ? `${base} ${formatSignedMoney(totalPnl)}` : base;
    return {
      price: lot.entry,
      title,
      dashed: index > 0,
    };
  });
  const markers = lots.flatMap((lot, index) => {
    if (!lot.time) return [];
    const label = index === 0 ? "Open" : `Add ${index}`;
    return [
      {
        time: lot.time as UTCTimestamp,
        position: barPos,
        shape: arrow,
        color,
        text: label,
      } satisfies SeriesMarker<UTCTimestamp>,
    ];
  });
  return { lines, markers };
}

function applyEntryDecorations(
  series: ISeriesApi<"Candlestick">,
  plugin: ISeriesMarkersPluginApi<UTCTimestamp> | null,
  existing: IPriceLine[],
  position: Position | null,
  knownTimes: Set<number>,
  price: number | null,
): IPriceLine[] {
  const decorations = entryDecorations(position, price);
  const color = position?.side === "short" ? SHORT_COLOR : LONG_COLOR;
  if (existing.length === decorations.lines.length && existing.length > 0) {
    existing.forEach((line, index) => {
      const next = decorations.lines[index];
      line.applyOptions({
        price: next.price,
        color,
        lineWidth: next.dashed ? 1 : 2,
        lineStyle: next.dashed ? LineStyle.Dashed : LineStyle.Solid,
        axisLabelVisible: true,
        title: next.title,
      });
    });
    plugin?.setMarkers(decorations.markers.filter((marker) => knownTimes.has(Number(marker.time))));
    return existing;
  }
  for (const line of existing) {
    series.removePriceLine(line);
  }
  const lines = decorations.lines.map((line) =>
    series.createPriceLine({
      price: line.price,
      color,
      lineWidth: line.dashed ? 1 : 2,
      lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
      axisLabelVisible: true,
      title: line.title,
    }),
  );
  plugin?.setMarkers(decorations.markers.filter((marker) => knownTimes.has(Number(marker.time))));
  return lines;
}

export default function Chart({ bars, overlays, position }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlaysRef = useRef<OverlaySeriesMap | null>(null);
  const macdHist = useRef<ISeriesApi<"Histogram"> | null>(null);
  const macdLine = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSignal = useRef<ISeriesApi<"Line"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<UTCTimestamp> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const positionRef = useRef(position);
  const barsRef = useRef(bars);
  const barCountRef = useRef(0);
  const sessionRef = useRef<number | null>(null);
  positionRef.current = position;
  barsRef.current = bars;

  useEffect(() => {
    if (!host.current) return;
    const instance = createChart(host.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0e1116" },
        textColor: "#8b98a8",
        fontFamily: "Inter, system-ui, sans-serif",
      },
      grid: {
        vertLines: { color: "#1b222c" },
        horzLines: { color: "#1b222c" },
      },
      rightPriceScale: { borderColor: "#242c36" },
      timeScale: {
        borderColor: "#242c36",
        timeVisible: true,
        secondsVisible: false,
        barSpacing: BAR_SPACING,
        minBarSpacing: 4,
        rightOffset: 0,
        shiftVisibleRangeOnNewBar: false,
      },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    const candle = instance.addSeries(CandlestickSeries, {
      upColor: "#00c853",
      downColor: "#ff4d4f",
      borderUpColor: "#00c853",
      borderDownColor: "#ff4d4f",
      wickUpColor: "#00c853",
      wickDownColor: "#ff4d4f",
    });
    overlaysRef.current = {
      ma20: instance.addSeries(LineSeries, { color: "#f5c542", lineWidth: 2, priceLineVisible: false, lastValueVisible: false }),
      ma60: instance.addSeries(LineSeries, { color: "#5c9dff", lineWidth: 2, priceLineVisible: false, lastValueVisible: false }),
      ema12: instance.addSeries(LineSeries, { color: "#ff8a65", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      ema26: instance.addSeries(LineSeries, { color: "#ce93d8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      bbMid: instance.addSeries(LineSeries, { color: "#90a4ae", lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false }),
      bbUpper: instance.addSeries(LineSeries, { color: "#78909c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
      bbLower: instance.addSeries(LineSeries, { color: "#78909c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }),
    };
    macdHist.current = instance.addSeries(
      HistogramSeries,
      { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 4, minMove: 0.0001 } },
      1,
    );
    macdLine.current = instance.addSeries(
      LineSeries,
      { color: "#42a5f5", lineWidth: 2, priceLineVisible: false, lastValueVisible: false },
      1,
    );
    macdSignal.current = instance.addSeries(
      LineSeries,
      { color: "#ffca28", lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
      1,
    );
    const panes = instance.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(0.74);
      panes[1].setStretchFactor(0.26);
    }
    chart.current = instance;
    candles.current = candle;
    markers.current = createSeriesMarkers(candle, []) as ISeriesMarkersPluginApi<UTCTimestamp>;
    return () => {
      instance.remove();
      chart.current = null;
      candles.current = null;
      overlaysRef.current = null;
      macdHist.current = null;
      macdLine.current = null;
      macdSignal.current = null;
      markers.current = null;
      priceLines.current = [];
      barCountRef.current = 0;
      sessionRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candles.current || !chart.current || !host.current || !overlaysRef.current) return;
    const previousRange = chart.current.timeScale().getVisibleLogicalRange();
    const previousCount = barCountRef.current;
    const session = bars[0]?.time ?? null;
    const newSession = session !== sessionRef.current || bars.length < previousCount;
    candles.current.setData(
      bars.map((bar) => ({
        time: bar.time as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    const overlay = overlaySeries(bars);
    const enabled = new Set(overlays);
    overlaysRef.current.ma20.setData(enabled.has("ma20") ? overlay.ma20 : []);
    overlaysRef.current.ma60.setData(enabled.has("ma60") ? overlay.ma60 : []);
    overlaysRef.current.ema12.setData(enabled.has("ema12") ? overlay.ema12 : []);
    overlaysRef.current.ema26.setData(enabled.has("ema26") ? overlay.ema26 : []);
    overlaysRef.current.bbMid.setData(enabled.has("bb20") ? overlay.bbMid : []);
    overlaysRef.current.bbUpper.setData(enabled.has("bb20") ? overlay.bbUpper : []);
    overlaysRef.current.bbLower.setData(enabled.has("bb20") ? overlay.bbLower : []);
    const macd = macdSeries(bars);
    macdHist.current?.setData(macd.hist);
    macdLine.current?.setData(macd.macd);
    macdSignal.current?.setData(macd.signal);
    if (newSession) {
      pinFromLeft(chart.current, host.current, bars.length);
    } else {
      restoreVisibleRange(chart.current, previousRange, previousCount, bars.length);
    }
    sessionRef.current = session;
    barCountRef.current = bars.length;
    priceLines.current = applyEntryDecorations(
      candles.current,
      markers.current,
      priceLines.current,
      positionRef.current,
      new Set(bars.map((bar) => bar.time)),
      lastPrice(bars),
    );
  }, [bars, overlays]);

  useEffect(() => {
    if (!candles.current) return;
    priceLines.current = applyEntryDecorations(
      candles.current,
      markers.current,
      priceLines.current,
      position,
      new Set(barsRef.current.map((bar) => bar.time)),
      lastPrice(barsRef.current),
    );
  }, [position]);

  return <div className="chart-el" ref={host} />;
}
