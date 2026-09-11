"""Serve local clean parquet bars to the replay UI.

Bars stay in UTC on disk; dates and clock labels are converted to
America/New_York so a session matches the exchange calendar.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

MARKET_TZ = "America/New_York"
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
BARS_ROOT = DATA_ROOT / "clean" / "bars"

app = FastAPI(title="sim-trader")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _parquet(symbol: str, timeframe: str, feed: str) -> Path:
    return BARS_ROOT / feed / timeframe / f"{symbol}.parquet"


@lru_cache(maxsize=16)
def _load(symbol: str, timeframe: str, feed: str) -> pd.DataFrame:
    path = _parquet(symbol, timeframe, feed)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return pd.read_parquet(path)


@app.get("/api/catalog")
def catalog() -> dict:
    symbols: dict[str, list[str]] = {}
    if not BARS_ROOT.exists():
        return {"feed": "iex", "timeframes": {}, "symbols": {}}
    for feed_dir in sorted(BARS_ROOT.iterdir()):
        if not feed_dir.is_dir():
            continue
        for tf_dir in sorted(feed_dir.iterdir()):
            if not tf_dir.is_dir():
                continue
            names = sorted(p.stem for p in tf_dir.glob("*.parquet"))
            symbols.setdefault(tf_dir.name, [])
            for name in names:
                if name not in symbols[tf_dir.name]:
                    symbols[tf_dir.name].append(name)
    return {"feed": "iex", "symbols": symbols}


@app.get("/api/days")
def days(symbol: str, timeframe: str = "1Min", feed: str = "iex") -> dict:
    try:
        bars = _load(symbol, timeframe, feed)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no dataset for {symbol} {timeframe}") from exc
    local = bars.index.tz_convert(MARKET_TZ)
    unique = sorted({stamp.date().isoformat() for stamp in local}, reverse=True)
    return {"symbol": symbol, "timeframe": timeframe, "days": unique}


@app.get("/api/bars")
def bars(symbol: str, day: str, timeframe: str = "1Min", feed: str = "iex") -> dict:
    try:
        frame = _load(symbol, timeframe, feed)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"no dataset for {symbol} {timeframe}") from exc
    try:
        target = pd.Timestamp(day).date()
    except ValueError as exc:
        raise HTTPException(400, "day must be YYYY-MM-DD") from exc

    local = frame.index.tz_convert(MARKET_TZ)
    mask = pd.Series([stamp.date() for stamp in local], index=frame.index) == target
    picked = frame.loc[mask]
    if picked.empty:
        raise HTTPException(404, f"no bars for {symbol} on {day}")

    rows = []
    for stamp, row in picked.iterrows():
        et = stamp.tz_convert(MARKET_TZ)
        rows.append(
            {
                "time": int(stamp.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "clock": et.strftime("%H:%M:%S"),
            }
        )
    return {
        "symbol": symbol,
        "day": day,
        "timeframe": timeframe,
        "bars": rows,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
