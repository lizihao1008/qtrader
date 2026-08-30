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
  analyze_episodes.py      collect trade windows, screen what separated wins from losses
  search.py                one hypothesis -> attribution -> trial ledger
  audit_execution.py       five checks that fills are realistic and the past is fixed
  kronos_confirm.py        score a strategy's entry candidates with Kronos, cache them
  analyze_confirmation.py  does a confirmation score predict candidate outcomes at all
  plot_setups.py           best/worst trades with their levels and Kronos forecasts
                           (any strategy; see docs/HOWTO-galleries.md)
src/qtrader/
  config.py                RunConfig / DataConfig / StrategyConfig (YAML -> dataclasses)
  runner.py                the pipeline order: config -> context -> strategy -> engine
  data/                    provider client, schema, sessions, panel, storage, ingest
  universe/                universe definition, per-bar tradability filters
  features/                stock indicators, relative/residual returns, cross-sectional ranks
  strategies/              market context -> target weights
  models/                  external forecasting models used as confirmation, never as strategies
  backtest/                costs, portfolio accounting, engine, metrics, signal metrics
  analysis/                trade episodes and conditional screening
  viz/                     plotly charts + HTML report
  experiments/             run manifests, artifact persistence, parameter sweeps
tests/unit, tests/integration
docs/                      context, progress, ADRs, changelog
data/, results/            generated, git-ignored
```

Not created yet (planned, see outline §15): `regime/`, `labels/`, `risk/`,
`execution/`.

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
* `trend.py` — two estimators of the same quantity, the drift in random-walk
  standard deviations. `drift_zscore` standardises a fixed-window OLS slope
  against its sampling distribution under a **driftless random walk** rather
  than the regression's own standard error (the textbook t-statistic assumes
  independent residuals and exceeds 2 on ~80% of random walks).
  `ewma_drift_zscore` does the same for an exponentially weighted drift, whose
  closed-form `S2` makes it calibrated from the session's second bar — no
  window, no blackout. `session_drift_zscore` measures drift since the open,
  `sum(r)/(sigma*sqrt(n))`, the only one whose window grows with the day and so
  the only one that resolves a slow sustained move (R03). `random_walk_slope_scale(W)` is the closed form for the
  OLS slope's sampling scale. Both are verified by simulation in `test_trend.py`.
* `momentum.py` — `session_macd` restarts the EMAs each session so an overnight
  gap cannot enter the oscillator, and `macd_cross_zscore` measures how sharply
  the histogram is moving in the same random-walk units: `dh` is a linear filter
  of returns, so `Var(dh) = (sigma*P)^2 ||g||^2` for the filter's impulse
  response `g`. `session_reset_norms` tabulates `||g||` per within-session
  position, because a restarted filter is not a converged one.
* `relative.py` — `bar_log_returns` (overnight gap zeroed), `align_reference`
  (each symbol paired with its own sector/benchmark series), `rolling_beta`
  (from rolling moments, clipped), `residual_returns`, `trailing_return`.
* `ranks.py` — cross-sectional rank / z-score / count, all requiring an
  eligibility mask so ineligible names never enter the moments.
* `levels.py` — ex-ante support/resistance. `average_true_range` takes
  `restart` so the previous close does not cross a session boundary; without it
  the first bar of a session has a true range equal to the overnight gap, and
  since ATR sets the break-zone width that puts yesterday's price into today's
  definition of a break (R11 §1). Families: previous-day high/low fixed at the
  prior close, opening-range high/low that does **not exist** until the range
  has closed, static round numbers, Wilder `average_true_range`, and
  `level_zone` = `max(1 tick, level_atr * ATR)` so a one-cent touch is not a
  break. Round levels must be *lagged by a bar* before use as a break
  reference — `nearest_round_levels` brackets the price it is given, so
  comparing a price to its own bracket is unsatisfiable by construction.
* `seasonality.py` — the intraday volatility U-shape. `seasonal_volatility`
  multiplies a rolling level estimate by a per-minute-of-session profile fitted
  on **completed prior sessions only**. A flat sigma understates the open by
  3–4x, which silently loosens every sigma-based threshold exactly when the
  market is wildest.

### `strategies/`
`Strategy.generate(MarketContext) -> StrategySignals`. Weights are validated to
be finite with `sum(|w|) <= 1`. `indicators` is a per-symbol dict carried
through to the charts, so the report draws exactly what the rule looked at;
`StrategySignals.stack(column)` turns one of those columns into a wide frame.
`Strategy.setup_features(signals, context)` is the optional hook that feeds
post-trade screening — scale-free, causal quantities only.
* `ma_cross.py` — single-name crossover, splits capital across the universe.
* `cross_sectional_residual.py` — residual → z-score → top/bottom-k → hold until
  the next session-anchored rebalance → flatten before the close. Exposes
  `mode` (reversion/momentum) and `reference` (sector/market) as competing
  hypotheses rather than hard-coding one.
* `trend_ratchet.py` — the one **path-dependent** strategy (ADR-0005). Entry is
  a **state** asked every bar, not a crossing event: significant drift
  (`trend_estimator` selects `session`, `ewma` or `window`), oscillator on the
  same side, optionally still accelerating (`min_cross_zscore`);
  exit is the monotone barrier `max(entry - s*sigma_H, extreme - r*sigma_H)`,
  where `sigma_H = sigma_bar * sqrt(horizon_bars)` also sizes the position so
  that `weight * stop_distance` is a fixed fraction of capital. `trend_z_reset`
  is a hysteresis band doing double duty: it releases an open position when `z`
  reaches it on the opposite side, and disarms a symbol against re-entry until
  `|z|` has cooled below it. Because the stop
  depends on the best price since entry, `_walk` is an explicit loop over bars,
  vectorised across symbols; everything else stays vectorised.

* `sr_momentum.py` — the second **path-dependent** strategy. A state machine per
  symbol: break an ex-ante level by more than its zone, retest it within
  `retest_bars`, close back on the breakout side, with momentum, relative volume
  and VWAP side agreeing. Released by the same ratchet as `trend_ratchet`. It
  publishes a `candidate` column of proposed entry directions, which is the
  convention that lets an expensive external model be scored once on candidates
  and cached instead of run on every bar. Measured null (R09).

### `models/`
External forecasting models, used **only** to veto entries a strategy already
proposed — never as signals of their own.
* `kronos_confirm.py` — `collect_candidates` reads a strategy's `candidate`
  column; `forecast_paths` returns the predicted OHLCV paths for charting;
  `score_candidates` batches context windows through an injected
  `KronosPredictor` and returns a timestamp x symbol score. **The context window
  ends at the candidate bar inclusive**; the forecast begins after it. The
  predictor is injected so the module has no torch dependency and is testable
  with a stub. Scores cache to parquet and reach a strategy through
  `confirmation_path`; a missing score is a refusal, not a pass.

  Both entry points share `_run_batches`, so the window slice — the causality
  guarantee — exists in exactly one place.

  Measured on 15,645 candidates: rank IC −0.011 against realised outcomes, 49%
  directional agreement. Null. See R09.

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

### `analysis/`
The reusable win/loss diagnosis. Nothing here knows about a specific strategy.

* `diagnostics.py` — **the entry point.** `diagnose(config, split=...)` runs the
  backtest, extracts episodes, and returns a `Diagnosis` exposing `.conditions`,
  `.contrast`, `.survivors`, `.threshold`, `.summary()`, `.save()` and
  `.write_report()`. Accepts a `RunConfig` or a path, an optional split and
  parameter overrides, and can reuse an already-executed `Run`.
* `features.py` — `market_features(context)` builds the universal, strategy-
  agnostic screen (volatility, residual volatility, beta, trailing return,
  relative volume, bar range, VWAP distance, sector return, time of day,
  breadth, market state, dispersion). `setup_features(strategy, signals,
  context)` merges it with whatever the strategy declares, the strategy winning
  a name clash and the universal version kept under a `market_` prefix. A
  `FeatureSet` holds per-symbol frames and shared series and can `sample` one
  symbol at one bar.
* `episodes.py` — `extract_episodes(run)` builds one episode per round trip: the
  K-line window (`context_bars` before the entry through the exit), the setup
  features read at the **decision bar** (`entry - execution_lag`, so the context
  is what the strategy actually had), and the outcome as gross, net, cost, MFE
  and MAE. `Episodes` records which columns are setup vs outcome and exposes
  `.screen()`, `.contrast()`, `.profile()` and `.threshold()`; `save/load`
  persists the tables plus those roles. `oriented_paths` normalises every window
  to bps from entry, signed so up is profit; `excursion_summary` contrasts how
  winners and losers reached their outcome.
* `conditions.py` — `rank_conditions` screens features against the outcome
  (Spearman, quantile profile, monotonicity), `outcome_contrast` compares
  winners against losers, and `bonferroni_t_threshold` states the bar a
  t-statistic must clear given how many features were tried.

**Adding a strategy requires no change here.** It is screened against the
universal features automatically; overriding `Strategy.setup_features` adds its
own view. See `test_diagnostics.py::test_the_same_call_works_on_a_different_strategy_unchanged`.

### `experiments/splits.py`
Named contiguous evaluation windows loaded from `config/splits.yaml`, each with
a purpose string. `apply_split` narrows a config and suffixes its `run_id` so
two windows cannot overwrite each other. See ADR-0004.

### `runner.py`
`build_context` and `execute` define what "running a backtest" means, so scripts,
sweeps and tests cannot drift apart. `execute` accepts a prebuilt context so a
sweep loads the parquet files once.

### `viz/`
`episodes.py` adds the post-trade views: `path_chart` (mean oriented path of
winners vs losers, truncated once fewer than 20% of episodes are still open, so
the tail is not a survivorship artifact), `episode_gallery` (best/worst trades
as normalised candlestick panels, shorts flipped so up is profit) and
`write_episode_report`.

`setups.py::setup_gallery` is the deliberate opposite normalisation: panels stay
in **real prices**, because a support level does not survive being rescaled per
panel, and shorts are not flipped. Each panel carries the level the rule was
watching with its tolerance zone shaded, an entry marker pointing the predicted
direction, and an external model's forecast path drawn forward from the decision
bar. Both the level and the forecast are read at **offset -1**, the decision
bar — at offset 0 the position is already open, the break state is consumed, and
reading there draws nothing while still producing a plausible-looking chart.

`price_chart` has a fixed four-row layout so **any** strategy charts readably:
price (candles + price-level indicator lines + BUY/SELL markers at executed
prices, capped at the most recent `MAX_CANDLES`), volume, MACD, and the
strategy's own panel (`score` / `trend_zscore` / `signal`) when it has one. The
MACD row is always present — a strategy's own MACD series are used if it
publishes them, otherwise a standard MACD(12,26,9) is computed in the chart and
labelled as a reference, so a chart never lacks the indicator a reader expects.
Alongside it,
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

## 3a. Known interaction: a veto filter is not a subtraction

`sr_momentum` caps the book at `max_positions`, and `_respect_book_limit` ranks
competing candidates by `|momentum_z|` and keeps the strongest. While that cap
binds, **vetoing an entry does not remove a trade — it frees a slot that a
weaker candidate fills.** Trade count and turnover go *up*, and measured
performance falls for reasons that have nothing to do with the filter's own
information. This has now confounded three separate experiments (R09, R10 §3,
R11 §2). Evaluate any filter both as a veto and as a ranking input, and always
report the trade count alongside the return.

## 3b. Known limitation: costs are flat, spreads are not

`costs.py` applies one `half_spread_bps` to every fill regardless of time of
day. Roll (1984) estimated on 1-minute bars for this universe gives a
half-spread of **4.91 bps in the first five minutes of the session against
0.34 bps between 11:30 and 14:30** — a factor of 14. The configured 1.0 bps is
therefore roughly 3x conservative mid-day and roughly 5x optimistic at the open.

Any strategy whose turnover concentrates in the first half hour has an inflated
backtest here. `sr_momentum` puts 49% of its entries in the second bar of the
session, and correcting for measured spreads moves it from +0.50 to
−3.09 bps/trade. See R10; a time-of-day cost model is the recorded next action.

## 4. Invariants worth protecting

1. No fill may use information from its own or a later bar.
2. Raw data is never modified in place.
3. All timestamps are UTC until the session module converts them.
4. Backtests are never frictionless.
5. `sum(trades.net_pnl) == equity change` for a run that ends flat.
6. Reference ETFs are never traded.
7. Cross-sectional statistics are computed over eligible symbols only.
8. Episode setup features are read at the decision bar, never the fill bar.
9. Setup features are comparable across symbols; raw price levels never enter a
   screen.
10. A statistic used as a filter must have a null distribution that holds for
    price data — see `drift_zscore` versus the regression t-statistic.

## 5. Known failure modes

* **IEX vs SIP** — free-tier bars miss most consolidated volume. This is the
  most likely reason a genuine intraday cross-sectional effect would fail to
  show up here, and it is not something more modelling can fix.
* **Short sample** — a few weeks of 1-minute data annualise to nonsense-looking
  Sharpe/Calmar figures, and cross-sectional IC estimated on it does not
  replicate (this happened; see PROGRESS.md).
* **Whole-share sizing** — a weight of 0.17 rounds very differently in a $600
  stock than in a $30 one.
* **Spurious regression** — prices are I(1), so any statistic whose standard
  error assumes independent residuals will call trends everywhere. This bit
  once, on the first draft of the trend filter, and the fix was a different
  scale rather than a different threshold.
* **Conditioning on outcomes** — holding period is partly an outcome (a position
  stays open because it keeps being re-selected), so it is not a setup feature
  and so is never exposed as a setup feature. The same trap catches anything
  measured after the entry — `Strategy.setup_features` must not return one.
* **Turnover** — at 1-minute resolution the default rebalance interval is the
  single biggest driver of net PnL. Always read `daily_turnover` next to
  `total_costs`.
* **Report size** — plotly.js is inlined and candles are capped, but a
  multi-month run still produces a several-MB HTML file.
