# C00 — Codebase Understanding

Top-down map of the repository. Updated whenever durable understanding changes.

## 1. Repository map

```text
config/
  universe/*.yaml          who is in the universe, benchmark, sector map
  backtest/*.yaml          one file per run: universe, window, strategy, costs, execution
scripts/                   the only entry points
  download_data.py         Alpaca -> raw -> clean, for a whole universe
  run_backtest.py          config -> results/<run_id>/
  sweep.py                 vary parameters over the same data
src/qtrader/
  config.py                RunConfig / DataConfig / StrategyConfig (YAML -> dataclasses)
  runner.py                the pipeline order: config -> context -> strategy -> engine
  data/                    provider client, schema, sessions, panel, storage, ingest
  universe/                universe definition, per-bar tradability filters
  features/                stock indicators, relative/residual returns, cross-sectional ranks
  strategies/              market context -> target weights
  backtest/                costs, portfolio accounting, engine, metrics, signal metrics
  viz/                     plotly charts + HTML report
  experiments/             run manifests, artifact persistence, parameter sweeps
tests/unit, tests/integration
docs/                      context, progress, ADRs, changelog
data/, results/            generated, git-ignored
```

Not created yet (planned, see outline §15): `regime/`, `labels/`, `models/`,
`risk/`, `execution/`.

## 2. End-to-end data flow

```text
AlpacaBarClient.fetch_bars_multi        batched, many symbols per request
      -> schema.coerce_bars             canonical layout, UTC
      -> schema.validate_bars           hard invariants (raises) + gap warnings
      -> BarStore.write_raw             immutable parquet + manifest
      -> ingest.build_clean             sessions.filter_regular_hours, re-validate
      -> BarStore.write_clean
                    |
      ingest.load_panel                 all symbols -> BarPanel on one timestamp grid
                    |
      LiquidityFilter.tradable          per-bar mask over stocks only
                    |
      MarketContext(panel, universe, tradable)
                    |
      Strategy.generate -> StrategySignals(target_weights, scores, indicators)
                    |
      BacktestEngine.run                shift by execution_lag_bars, size from equity,
                                        fill via CostModel, Portfolio books cash/positions,
                                        mark to close each bar
                    |
      BacktestResult(equity_curve, fills, trades, metrics)
      + signal_metrics.rank_ic_summary  (added by runner.execute)
                    |
      experiments.save_run -> results/<run_id>/{csv, json, report.html}
                              viz.report.write_report -> equity, exposure, K-lines
```

## 3. Module notes

### `data/schema.py`
`coerce_bars` reshapes only (never repairs). `validate_bars` owns the definition
of valid data: duplicate/unsorted timestamps, NaNs, impossible OHLC,
non-positive prices, negative volume are **errors**; large timestamp gaps are
**warnings** (normal across session breaks).

### `data/sessions.py`
The only place UTC is converted to `America/New_York`. Provides
`filter_regular_hours`, `session_date` (the intraday grouping key everything
else groups by) and `at_or_after_market_time` (clock-based flattening — causal,
and identical in backtest and live).

### `data/panel.py`
`BarPanel.from_frames` aligns symbols on the union index, forward-fills prices
within sessions, zero-fills volume, and keeps `traded` / `available` masks.
`bars(symbol)` rebuilds a single-symbol frame for charting. This is the only
structure cross-sectional code should touch.

### `data/storage.py`
`DatasetKey(symbol, timeframe, feed)` addresses a dataset. Raw writes merge and
raise `RawDataConflict` if stored values would change; clean writes overwrite
because clean is always rebuildable from raw.

### `universe/`
`Universe` names the tradable symbols, the benchmark and the sector map;
`all_symbols` is what must be downloaded, `symbols` is what may be held.
`require_sectors()` fails loudly for strategies that need peers.
`LiquidityFilter.tradable(panel)` answers "could this be traded at this bar?"
using price, trailing median dollar volume and staleness — all causal.

### `features/`
* `stock.py` — causal per-symbol indicators (SMA, EMA, MACD, log return,
  realized vol, session-resetting intraday VWAP).
* `relative.py` — `bar_log_returns` (overnight gap zeroed), `align_reference`
  (each symbol paired with its own sector/benchmark series), `rolling_beta`
  (from rolling moments, clipped), `residual_returns`, `trailing_return`.
* `ranks.py` — cross-sectional rank / z-score / count, all requiring an
  eligibility mask so ineligible names never enter the moments.

### `strategies/`
`Strategy.generate(MarketContext) -> StrategySignals`. Weights are validated to
be finite with `sum(|w|) <= 1`. `indicators` is a per-symbol dict carried
through to the charts, so the report draws exactly what the rule looked at.
* `ma_cross.py` — single-name crossover, splits capital across the universe.
* `cross_sectional_residual.py` — residual → z-score → top/bottom-k → hold until
  the next session-anchored rebalance → flatten before the close. Exposes
  `mode` (reversion/momentum) and `reference` (sector/market) as competing
  hypotheses rather than hard-coding one.

### `backtest/`
* `costs.py` — `CostModel.fill_price` (half spread + slippage, direction-aware)
  and `commission` (per-share + bps + floor).
* `portfolio.py` — cash and per-symbol positions. Folds fills into per-symbol
  lots and emits a `Trade` per round trip, splitting costs proportionally on
  partial closes and reversals. `net_pnl` reconciles with the cash change.
* `engine.py` — the timing loop and the central invariant. Trades only on weight
  changes; refuses to open exposure in a symbol that did not print.
* `metrics.py` — trading-time annualisation, Sharpe/Sortino/Calmar, drawdown,
  gross/net exposure, position count, hit rate, profit factor, daily turnover.
* `signal_metrics.py` — forward returns (labels, evaluation only) and rank IC
  by horizon.

### `runner.py`
`build_context` and `execute` define what "running a backtest" means, so scripts,
sweeps and tests cannot drift apart. `execute` accepts a prebuilt context so a
sweep loads the parquet files once.

### `viz/`
`price_chart` (candles + indicators + volume + MACD-or-score panel + BUY/SELL
markers at executed prices, capped at the most recent `MAX_CANDLES`),
`equity_chart` (cumulative return vs benchmark + drawdown) and `exposure_chart`
(gross/net exposure + position count). Long runs are bucketed to
`MAX_LINE_POINTS` — last value for paths, minimum for drawdown, so the worst
point is never smoothed away. `report.write_report` assembles one self-contained
HTML file with plotly.js inlined exactly once, by the first figure in document
order.

### `experiments/`
`registry.save_run` writes CSVs, `metrics.json`, `report.html` and
`manifest.json`. `sweep.sweep` re-runs one strategy across a parameter grid on
shared data and returns one row of metrics per combination.

## 4. Invariants worth protecting

1. No fill may use information from its own or a later bar.
2. Raw data is never modified in place.
3. All timestamps are UTC until the session module converts them.
4. Backtests are never frictionless.
5. `sum(trades.net_pnl) == equity change` for a run that ends flat.
6. Reference ETFs are never traded.
7. Cross-sectional statistics are computed over eligible symbols only.

## 5. Known failure modes

* **IEX vs SIP** — free-tier bars miss most consolidated volume. This is the
  most likely reason a genuine intraday cross-sectional effect would fail to
  show up here, and it is not something more modelling can fix.
* **Short sample** — a few weeks of 1-minute data annualise to nonsense-looking
  Sharpe/Calmar figures, and cross-sectional IC estimated on it does not
  replicate (this happened; see PROGRESS.md).
* **Whole-share sizing** — a weight of 0.17 rounds very differently in a $600
  stock than in a $30 one.
* **Turnover** — at 1-minute resolution the default rebalance interval is the
  single biggest driver of net PnL. Always read `daily_turnover` next to
  `total_costs`.
* **Report size** — plotly.js is inlined and candles are capped, but a
  multi-month run still produces a several-MB HTML file.
