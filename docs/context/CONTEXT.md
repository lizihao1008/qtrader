# Stable Project Context

Assumptions that hold across tasks. Edit in place when one stops being true —
Git keeps the old version. Transient notes belong in `docs/progress/PROGRESS.md`.

## 1. Scope

Intraday US-equity research system (`qtrader`). Current stage: deterministic
cross-sectional baseline over a 22-name universe. No machine learning yet, no
live trading.

## 2. Data

* Provider: **Alpaca** (`alpaca-py`), credentials from `ALPACA_API_KEY` /
  `ALPACA_SECRET_KEY` environment variables only — never in config or Git.
* Feed: `iex` (free tier) by default. IEX carries only a small share of
  consolidated volume, so its minute bars are a noisy proxy for the true tape.
  This matters most for volume features and for anything microstructural. A
  dataset records which feed produced it; the two must not be mixed in one study.
* Resolution: 1-minute OHLCV bars.

### Canonical bar frame

Index `timestamp`, tz-aware **UTC**, unique, strictly increasing; timestamp is
the bar's **open** time. Columns `open, high, low, close, volume`
(+ `trade_count`, `vwap` when supplied), all float64.
Defined and enforced in `src/qtrader/data/schema.py`.

### Panel

Cross-sectional work uses `BarPanel` (`src/qtrader/data/panel.py`): every symbol
aligned on the union timestamp grid, each field exposed as a wide
`timestamp x symbol` frame. A minute with no prints is filled **within its own
session** with the last close (`open = high = low = close`, `volume = 0`);
forward filling never crosses a session boundary. Two masks describe reality:
`traded` (a real bar arrived) and `available` (a price is known).

### Storage layers

```text
data/raw/bars/{feed}/{timeframe}/{symbol}.parquet     exactly as received (immutable)
data/clean/bars/{feed}/{timeframe}/{symbol}.parquet   validated, regular hours only
```

Each parquet has a `.json` sidecar manifest. Re-downloading an overlapping
window merges; if the provider reports *different* values for a stored
timestamp the write is refused (`RawDataConflict`). Nothing under `data/` or
`results/` enters Git.

## 3. Time conventions

* Everything is stored and computed in **UTC**.
* Trading-day logic (regular hours, intraday VWAP reset, session-anchored
  rebalancing, end-of-day flattening) converts to **America/New_York** in
  `src/qtrader/data/sessions.py` and nowhere else.
* Regular trading hours: `[09:30, 16:00)` exchange-local, weekdays.
* Exchange holidays are not modelled; the provider returns no bars, which
  behaves like an empty session.
* Annualisation uses **trading time** (252 sessions/year, bars-per-session
  inferred from the data), never wall-clock time.
* The **overnight gap is excluded** from intraday returns: the first bar of each
  session has a 1-bar return of 0. The gap is news, not intraday drift, and
  belongs in its own feature.

## 4. Universe

* Defined by a YAML file (`config/universe/*.yaml`): tradable `symbols`, a
  `benchmark` (SPY), and a `sectors` map from each stock to its sector ETF.
* Reference ETFs are **data, never positions** — the engine rejects a strategy
  that asks for a weight in one.
* Membership is **static**. Safe for the intraday windows used so far; it must
  become time-aware before any multi-year study, or it carries survivorship bias.
* Tradability is decided per bar by `LiquidityFilter`: price floor, trailing
  median dollar volume, and a staleness rule (no print in the last N bars ⇒ not
  tradable). Cross-sectional ranks are computed over eligible symbols only.

## 5. Anti-lookahead invariants

1. Features at bar `t` use only bars `<= t` (`qtrader.features`); centered
   windows, `bfill` and full-sample normalisation are forbidden.
2. A strategy's `target_weights.loc[t]` is decided at bar `t`'s **close**.
3. The engine fills at bar `t+1`'s **open**, net of costs
   (`execution_lag_bars >= 1`, enforced in `ExecutionConfig`).
4. Position sizing uses equity marked at the previous bar's close.
5. Session boundaries and rebalance grids come from the clock, not from
   neighbouring rows.
6. Forward returns exist only in `backtest/signal_metrics.py`, are labelled as
   labels, and never reach a strategy.

Regression tests: `test_engine.py::test_future_bars_cannot_change_past_fills`,
`test_universe.py::test_the_filter_never_looks_ahead`,
`test_features.py::test_indicators_do_not_change_when_future_bars_change`.

## 6. Strategy contract

* `Strategy.generate(MarketContext) -> StrategySignals`.
* `target_weights` is a `timestamp x symbol` frame of **signed fractions of
  deployed capital**, with `sum(|w|) <= 1` per row. A row of zeros is an
  explicit **no-trade** state.
* `ExecutionConfig.gross_leverage` decides how much capital "1" is.
* `scores` (optional) is the alpha score the weights came from; it drives the
  rank-IC diagnostics and is never used for sizing.

## 7. Alpha definition currently in use

* Residual return = stock return − (trailing beta) × (sector ETF return). The
  sector ETF is the V1 peer group; the beta absorbs what the sector alone does
  not. Betas are clipped to ±3 to survive near-zero reference variance.
* Scores are cross-sectional z-scores of accumulated residual return, computed
  only over symbols eligible at that bar.
* Signal quality is judged by **rank IC against the cross-sectionally demeaned
  forward return**, not by the equity curve. Reported t-statistics overstate
  significance because forward windows overlap.

## 7b. Trend definition in use

"A trend" means the trailing drift divided by its sampling scale **under a
driftless random walk** (`qtrader.features.trend`), from a fixed-window OLS fit
(`drift_zscore`), an exponentially weighted one (`ewma_drift_zscore`), or the
drift since the session open (`session_drift_zscore`, the default — the only one
whose window grows with the day and therefore the only one that resolves a slow
sustained move). The regression's own t-statistic is never used — on I(1) data it
exceeds 2 about 80% of the time.

The same principle standardises anything built from a linear filter of returns:
`qtrader.features.momentum.macd_cross_zscore` measures how sharply a MACD
crossing is happening in the same units, via the filter's impulse-response norm.

Volatility itself is **time-of-day aware** (`qtrader.features.seasonality`): the
per-minute standard deviation runs ~4x the session mean at the open and ~0.75x
by midday, so a flat estimate loosens every sigma-based threshold precisely at
the open. The profile is fitted on completed prior sessions only.

The same per-bar sigma also defines the risk unit
`sigma_H = sigma_bar * sqrt(horizon)`, so trend strength, crossing sharpness and
stop distance are all measured in one currency.

## 8. Execution and cost assumptions

* The engine trades a symbol **only when its target weight changes**; it never
  re-sizes a held position as equity drifts.
* It **cannot open or increase** exposure in a symbol that did not print in the
  execution bar (no fill without a trade). Closing such a position is allowed
  and counted in `metrics['stale_exits']`.
* Every fill pays half-spread + slippage (bps of the reference price) and
  commission. Defaults: 1.0 bps half spread, 0.5 bps slippage, zero commission
  (Alpaca US equities). At current sizing that is roughly **$5 per round trip
  per $16k of notional** — the number every intraday edge must clear.
* Trade PnL is decomposed: `net_pnl = gross_pnl - commission - slippage_cost`,
  where `gross_pnl` uses pre-cost reference prices. `net_pnl` summed over a
  flat-ending run equals the realised change in equity.
* Short selling is simulated without borrow fees or locate constraints — short
  results are optimistic until that is modelled.
* **Stops and trailing exits are strategy logic, not engine features**
  (ADR-0005). A barrier is evaluated on the bar's close and filled at the next
  open like any other signal, so a gap through a stop loses more than one risk
  unit. No backtest here fills at the barrier price inside a bar.

## 9. Evaluation windows

Named, contiguous windows in `config/splits.yaml`, fixed before the analysis
that uses them (ADR-0004). Each carries a purpose string that the tooling
prints:

* `mine` (2026-02-02 → 2026-06-01) — hypothesis generation, in-sample by
  definition;
* `validate` (2026-06-01 → 2026-07-28) — one confirmation, then spent. It has
  been spent for `cross_sectional_residual` (see docs/research/R01);
* `burned` (2026-07-28 → 2026-08-26) — the window the current `lookback` and
  `rebalance_bars` were chosen on. It cannot test those parameters.

Random splits of time-series rows are forbidden.

## 10. Post-trade analysis

`qtrader.analysis.diagnose(config, split=...)` turns a config into **episodes**:
one per round trip, holding the K-line window from 60 bars before the entry to
the exit, the setup features measured at the *decision* bar, and the outcome
split into gross and net.

It is **strategy-agnostic by construction**. Every run is screened against the
same universal market features (volatility, residual volatility, beta, trailing
return, relative volume, bar range, VWAP distance, sector return, time of day,
breadth, market state, cross-sectional dispersion). A strategy may add its own
by overriding `Strategy.setup_features`, which must return quantities that are
**comparable across symbols** (z-scores, ratios, counts, basis points — never
raw price levels) and **causal**. Nothing in the analysis layer changes when a
strategy is added. Costs are near-constant per round trip, so "did the signal work" is a
question about gross return and "did the strategy make money" is a question
about net; the two are reported separately everywhere.

Screening many features against one outcome manufactures significance, so
`bonferroni_t_threshold` is reported alongside every screen and a candidate is
confirmed on a different window before it changes anything.

## 11. Reproducibility

Every run is fully specified by one YAML config (`config/backtest/*.yaml`) and
writes `results/<run_id>/manifest.json` with config, universe, git commit, data
range and metrics. A result without a manifest is not a valid experiment.

Parameter sensitivity is checked with `qtrader.experiments.sweep`, and any
result worth believing must be confirmed on a window the parameters were not
chosen on.

## 12. Deferred by design

Dynamic/statistical peer groups, the regime layer, supervised labels, ML models,
the risk module and execution/broker integration are **not implemented yet**.
Their package boundaries are described in the outline and will be added in that
order (M2 → M6).
