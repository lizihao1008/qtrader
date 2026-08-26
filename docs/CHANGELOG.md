# Changelog

## 2026-08-26 (M1 — cross-sectional pipeline)

### Added

- `qtrader.data.panel.BarPanel` — multi-symbol alignment on one timestamp grid,
  session-bounded forward fill, `traded` / `available` masks.
- `qtrader.universe` — `Universe` (symbols, benchmark, sector map) and
  `LiquidityFilter` (price floor, trailing median dollar volume, staleness),
  evaluated per bar and causally.
- `qtrader.features.relative` — overnight-gap-free log returns, per-symbol
  reference alignment, rolling beta from rolling moments, residual returns.
- `qtrader.features.ranks` — cross-sectional rank / z-score / count, all
  requiring an eligibility mask.
- `qtrader.strategies.cross_sectional.CrossSectionalResidualStrategy` — the
  project's first cross-sectional alpha rule: sector-residual return, ranked
  across the universe, top/bottom-k held between session-anchored rebalances,
  with an explicit no-trade threshold and configurable reversion/momentum mode.
- `qtrader.backtest.signal_metrics` — forward returns (labels, evaluation only)
  and rank IC by horizon, reported in every run.
- `qtrader.runner` — one definition of the pipeline shared by scripts, sweeps
  and tests.
- `qtrader.experiments.sweep` and `scripts/sweep.py` — parameter sensitivity
  over shared data.
- `AlpacaBarClient.fetch_bars_multi` and `ingest.ingest_symbols` — batched
  multi-symbol download; `ingest.load_panel` for aligned loading.
- `viz.exposure_chart`; score panel in the price chart; per-symbol attribution
  and rank-IC tables in the report.
- Configs: `config/universe/us_liquid_22.yaml`, `single_pltr.yaml`,
  `config/backtest/xsec_reversion.yaml`, `xsec_reversion_oos.yaml`.
- Tests for the panel, universe/filters, relative features, ranks, signal
  metrics and charts (84 total).

### Changed

- **Strategy interface** (see ADR-0003): `Strategy.generate` takes a
  `MarketContext` and returns `target_weights` (`timestamp x symbol`, signed
  fractions of deployed capital, `sum(|w|) <= 1`) instead of a single-symbol
  `target_position` series. `ExecutionConfig.position_fraction` became
  `gross_leverage`.
- `BacktestEngine` and `Portfolio` hold many symbols at once; the engine refuses
  to open exposure in a symbol that did not print in the execution bar, and
  rejects weights on reference ETFs.
- Run configs name a `universe:` file instead of a single `data.symbol`.
- Metrics: added gross/net exposure, average positions, daily turnover; the
  buy-and-hold comparison is now the universe benchmark.
- Charts cap candles and bucket long lines (drawdown by minimum), cutting a
  six-month report from 29 MB to 5.7 MB.

### Removed

- The single-symbol engine, portfolio and `Strategy` interface, replaced rather
  than kept alongside (CLAUDE.md §9). `ma_cross` was migrated.

### Validation

- `pytest` — 84 passed.
- Real-data runs on 1-minute IEX bars, universe `us_liquid_22`:
  - in-sample 2026-07-28 → 2026-08-25 (21 sessions): +4.16% net, Sharpe 4.4,
    max drawdown −2.5%, turnover 7.3x, rank IC +0.013/+0.022/+0.029.
  - out-of-sample 2026-02-02 → 2026-07-28 (121 sessions): **−14.86% net**,
    gross PnL −$2,309, rank IC ≈ 0 and sign-unstable.
  - Conclusion recorded in PROGRESS.md: the in-sample edge did not replicate.
- Regression: `ma_cross` on PLTR still runs on the new architecture (+6.41%).
- Cost model verified analytically: measured cost per round trip equals
  `notional x impact_bps x 2 legs`.

## 2026-08-26 (M0 — repository and data foundation)

### Added

- Project skeleton: `pyproject.toml`, `.gitignore`, README, docs bootstrap
  (`CONTEXT.md`, `C00_CODEBASE.md`, `PROGRESS.md`, ADRs).
- `qtrader.data` — canonical 1-minute bar schema with validation
  (duplicate/unsorted timestamps, NaNs, impossible OHLC, negative volume),
  session/timezone module (UTC storage, `America/New_York` session logic),
  Alpaca client wrapper, immutable raw + rebuildable clean parquet store,
  ingest pipeline.
- `qtrader.features.stock` — causal SMA, EMA, MACD, log return, realized
  volatility, session-resetting intraday VWAP.
- `qtrader.strategies` — `Strategy` interface returning target positions in
  `{-1, 0, +1}`, `MACrossStrategy` deterministic baseline, name registry.
- `qtrader.backtest` — cost model (half spread, slippage, commission),
  portfolio/round-trip accounting with cost decomposition, bar-by-bar engine
  with an explicit signal-to-fill delay, performance metrics.
- `qtrader.viz` — candlestick chart with executed buy/sell markers, indicators,
  volume and MACD; cumulative-return vs buy & hold chart with drawdown;
  self-contained HTML report.
- `qtrader.experiments.save_run` — per-run manifest (config, git commit, data
  range, metrics) plus equity/trade/fill CSVs and the report.
- `config/backtest/ma_cross_pltr.yaml`, `scripts/download_data.py`,
  `scripts/run_backtest.py`.
- Tests: schema, sessions, feature causality, cost model, portfolio accounting,
  engine timing, strategy rules, end-to-end pipeline (41 tests).

### Fixed

- Engine re-sized held positions every bar, emitting ~590 one-share rounding
  trades on a 21-session run. It now trades only when the target exposure
  changes (regression test added).
- Round-trip PnL double-counted spread: gross PnL is now measured on pre-cost
  reference prices, with spread/slippage and commission reported separately, so
  `net_pnl` reconciles exactly with the cash change.

### Validation

- `pytest` — 41 passed.
- Real-data run: PLTR 1-minute IEX, 2026-07-28 → 2026-08-25, 8,160 bars.
  MA(20/60) long-only, flat at 15:55, 1.5 bps impact per fill →
  +6.02% net vs +37.44% frictionless buy & hold, 95 trades, −5.47% max
  drawdown, $2,867 costs.
- No-lookahead: bars after time T do not change any fill at or before T.
- Accounting: realised trade PnL equals the equity change on a flat-ending run.
