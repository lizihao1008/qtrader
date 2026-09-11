# Sim Trader

Interactive 1-minute candle replay for local Alpaca bars. Dark trading layout,
market orders only, no broker connection.

## Run

From the repo, with the `quant` conda env available:

```bash
cd sim-trader
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

The API reads `../data/clean/bars/iex/1Min/*.parquet`. Default instrument is QQQ.

## How it works

1. Pick a symbol and a session date.
2. The chart starts at the cash open; later bars are hidden.
3. Play / Pause and 1×–10× control how fast new candles appear.
4. Long / Short fills at the last visible close. Close realises P&L.
5. Starting cash is $10,000. Equity and return update on every bar.
