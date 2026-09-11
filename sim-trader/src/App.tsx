import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchBars, fetchCatalog, fetchDays } from "./api";
import Bottom from "./components/Bottom";
import Chart from "./components/Chart";
import Header from "./components/Header";
import IndicatorBar from "./components/IndicatorBar";
import PlaybackBar from "./components/PlaybackBar";
import TradePanel from "./components/TradePanel";
import {
  INITIAL_CASH,
  addMargin,
  equity as markEquity,
  intervalMs,
  openPosition,
  reduceMargin,
  unrealizedPnl,
  visibleBars,
} from "./engine";
import type { Bar, ClosedTrade, Position, Side } from "./types";
import type { OverlayId } from "./indicators";

export default function App() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbol, setSymbol] = useState("QQQ");
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState("");
  const [bars, setBars] = useState<Bar[]>([]);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [amount, setAmount] = useState(500);
  const [cash, setCash] = useState(INITIAL_CASH);
  const [position, setPosition] = useState<Position | null>(null);
  const [history, setHistory] = useState<ClosedTrade[]>([]);
  const [overlays, setOverlays] = useState<OverlayId[]>(["ma20", "ma60"]);
  const [error, setError] = useState("");
  const nextId = useRef(1);

  const shown = useMemo(() => visibleBars(bars, cursor), [bars, cursor]);
  const last = shown.at(-1) ?? null;
  const price = last?.close ?? null;
  const clock = last?.clock ?? "";
  const pnl = price == null ? 0 : unrealizedPnl(position, price);
  const equity = price == null ? cash : markEquity(cash, position, price);

  useEffect(() => {
    fetchCatalog()
      .then((catalog) => {
        const names = catalog.symbols["1Min"] ?? [];
        setSymbols(names);
        setSymbol((current) => (names.includes(current) ? current : names[0] ?? current));
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!symbol) return;
    fetchDays(symbol)
      .then((list) => {
        setDays(list);
        setDay((current) => (list.includes(current) ? current : list[0] ?? ""));
      })
      .catch((err: Error) => setError(err.message));
  }, [symbol]);

  const loadDay = useCallback(async (nextSymbol: string, nextDay: string) => {
    if (!nextSymbol || !nextDay) return;
    setPlaying(false);
    setError("");
    try {
      const loaded = await fetchBars(nextSymbol, nextDay);
      setBars(loaded);
      setCursor(loaded.length ? 1 : 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Load failed");
      setBars([]);
      setCursor(0);
    }
  }, []);

  useEffect(() => {
    void loadDay(symbol, day);
  }, [symbol, day, loadDay]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setCursor((current) => {
        if (current >= bars.length) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, intervalMs(speed));
    return () => window.clearInterval(timer);
  }, [playing, speed, bars.length]);

  function open(side: Side) {
    if (price == null || last == null) return;
    if (position) {
      if (position.side !== side) {
        setError("Close or reduce the current position first.");
        return;
      }
      const result = addMargin(cash, position, amount, price, clock, last.time);
      if ("error" in result) {
        setError(result.error);
        return;
      }
      setError("");
      setCash(result.cash);
      setPosition(result.position);
      return;
    }
    if (amount > cash) {
      setError("Not enough free cash for this margin.");
      return;
    }
    const opened = openPosition(side, amount, price, clock, last.time);
    if ("error" in opened) {
      setError(opened.error);
      return;
    }
    setError("");
    setCash((value) => value - amount);
    setPosition(opened);
  }

  function resetAccount() {
    setPlaying(false);
    setError("");
    setCash(INITIAL_CASH);
    setPosition(null);
    setHistory([]);
  }

  function recordClose(
    side: Side,
    openedAt: string,
    closedQty: number,
    closedMargin: number,
    closedEntry: number,
    fill: number,
    pnl: number,
  ) {
    setHistory((rows) => [
      ...rows,
      {
        id: nextId.current++,
        openedAt,
        closedAt: clock,
        side,
        qty: closedQty,
        margin: closedMargin,
        entry: closedEntry,
        exit: fill,
        pnl,
      },
    ]);
  }

  function reducePosition() {
    if (!position || price == null) return;
    const result = reduceMargin(cash, position, amount, price);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setError("");
    setCash(result.cash);
    setPosition(result.position);
    recordClose(
      position.side,
      position.openedAt,
      result.closedQty,
      result.closedMargin,
      result.closedEntry,
      result.fill,
      result.pnl,
    );
  }

  function closePosition() {
    if (!position || price == null) return;
    const result = reduceMargin(cash, position, position.margin, price);
    if ("error" in result) return;
    setError("");
    setCash(result.cash);
    setPosition(result.position);
    recordClose(
      position.side,
      position.openedAt,
      result.closedQty,
      result.closedMargin,
      result.closedEntry,
      result.fill,
      result.pnl,
    );
  }

  return (
    <div className="app">
      <div className="top">
        <Header
          symbols={symbols}
          symbol={symbol}
          days={days}
          day={day}
          price={price}
          clock={clock}
          cash={cash}
          equity={equity}
          initial={INITIAL_CASH}
          position={position}
          onSymbol={setSymbol}
          onDay={setDay}
          onReset={resetAccount}
        />
        {error ? <div className="error">{error}</div> : null}
      </div>
      <div className="workspace">
        <div className="chart-wrap">
          <IndicatorBar
            overlays={overlays}
            onToggle={(id) =>
              setOverlays((current) =>
                current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
              )
            }
          />
          <Chart bars={shown} overlays={overlays} position={position} />
          {shown.length === 0 ? <div className="overlay-note">Select a session and press Play</div> : null}
        </div>
        <TradePanel
          amount={amount}
          price={price}
          cash={cash}
          position={position}
          pnl={pnl}
          canTrade={price != null}
          onAmount={setAmount}
          onLong={() => open("long")}
          onShort={() => open("short")}
          onReduce={reducePosition}
          onClose={closePosition}
        />
      </div>
      <PlaybackBar
        playing={playing}
        speed={speed}
        cursor={cursor}
        total={bars.length}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onSpeed={setSpeed}
        onReset={() => void loadDay(symbol, day)}
      />
      <Bottom position={position} price={price} pnl={pnl} history={history} />
    </div>
  );
}
