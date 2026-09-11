import type { Bar } from "./types";

export async function fetchCatalog(): Promise<{ symbols: Record<string, string[]> }> {
  const res = await fetch("/api/catalog");
  if (!res.ok) throw new Error("Failed to load catalog");
  return res.json();
}

export async function fetchDays(symbol: string, timeframe = "1Min"): Promise<string[]> {
  const params = new URLSearchParams({ symbol, timeframe });
  const res = await fetch(`/api/days?${params}`);
  if (!res.ok) throw new Error("Failed to load days");
  const body = await res.json();
  return body.days as string[];
}

export async function fetchBars(
  symbol: string,
  day: string,
  timeframe = "1Min",
): Promise<Bar[]> {
  const params = new URLSearchParams({ symbol, day, timeframe });
  const res = await fetch(`/api/bars?${params}`);
  if (!res.ok) throw new Error(`No bars for ${symbol} on ${day}`);
  const body = await res.json();
  return body.bars as Bar[];
}
