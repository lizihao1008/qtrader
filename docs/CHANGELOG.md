# Changelog

## 2026-08-26 (what is left, and what would break through)

### Findings

- `docs/research/R06-what-is-left.md` — every remaining possibility in the
  current data assessed against the gate.
  - **IEX coverage measured: 2.1% of AAPL's tape** (13,268 shares per 5-minute
    bar against ~640,000 consolidated), median trade size 76 shares. This is the
    root cause behind several rejections.
  - `trade_count` / average trade size rejected: bar data cannot sign the flow,
    and a 2% retail-skewed sample is the wrong population for inferring
    institutional footprints.
  - `vwap`, ETF-constituent dislocation and sector lead-lag all rejected on
    incremental information or on being a microsecond taker's game.
  - Overnight/intraday decomposition is the only survivor and is marginal: it
    passes mechanism, side-of-trade and cost, fails incremental information, and
    is a known risk premium rather than an alpha.
  - ML/deep learning on price bars rejected as a class — same inputs, more
    parameters, which is the failure mode the gate exists to prevent.
- Recommendation recorded: move to daily-horizon cross-sectional US equity,
  which fixes the cost-to-holding-period ratio rather than working around it.
  Trend following's honest venue is futures; R04/R05's negative result is
  venue-specific.

## 2026-08-26 (falsification-first: a gate in front of the backtester)

### Added

- `docs/research/GATE.md` — what an idea must survive before any strategy code
  is written: a named mechanism with a counterparty, a reason it has not been
  arbitraged away, **which side of the trade is paid and whether this project's
  execution can be on that side**, an edge stated before backtesting that is
  several times the ~2.4 bps round trip, incremental information rather than
  another transform of past prices, and a named failure regime.
- `docs/adr/ADR-0006-falsification-first.md`.

### Changed

- Existing strategies relabelled rather than removed. `ma_cross` is a plumbing
  fixture, not a candidate. `trend_ratchet` and `cross_sectional_residual` are
  retired as candidates and retained as worked examples with their
  falsifications documented.
- Rejected as a class without further testing: oscillator and moving-average
  variants, breakout rules, and further reparameterisations of trailing-return
  signals. They are not new experiments.

### Findings

- The reversal mechanism is weaker than it first appeared. Inventory
  compensation predicts the edge should scale with the spread; measured, the
  spread more than doubles (1.04 → 2.42 bps) while the edge moves 14%
  (+2.90 → +3.30 bps). The pure liquidity-provision story does not fit, which
  under the gate is a rejection rather than a footnote.
- Applied honestly the gate rejects everything in the repository, and would
  reject most ideas proposable within intraday US large caps on IEX data. The
  binding constraint is the venue, not idea generation.

## 2026-08-26 (do trends exist? shuffle test)

### Validation

- `docs/research/R05-do-trends-exist.md` — tested the claim that sustained runs
  imply positive momentum expectancy, by counting 2-sigma/12-bar moves against
  two nulls.
  - The market produces **1.76x** as many sustained trends as a Gaussian random
    walk of identical volatility. Trends really are more common than chance.
  - But shuffling the *order* of the real returns within each day — preserving
    the exact returns, tails and that day's volatility — produces **3.23x**.
    Real / shuffled = **0.543**: the actual time ordering *suppresses* trends.
    The excess is fat tails and volatility clustering, not direction.
  - Up-onsets are separable from down-onsets at |t| up to 9.3 but **AUC 0.466 to
    0.528** — statistically detectable, economically a coin flip, and measured
    conditional on knowing a large move occurs.
  - Momentum rank IC is negative in every volatility regime tested (t −0.3 to
    −11.2).
  - Same data, same method: realised volatility is predictable from trailing
    volatility at **IC +0.67**; signed return from trailing return at **−0.023**.
    Magnitude is predictable, direction is not.

## 2026-08-26 (5-minute migration, execution audit, research loop)

### Added

- 5-minute dataset: 2024-01-02 → 2026-08-26, 664 sessions, 1.56M clean bars, and
  `config/backtest/trend_ratchet_5min.yaml` carrying the full timeframe audit —
  which parameters were rescaled to the same wall-clock span, which are per-fill
  or dimensionless and deliberately were not.
- `config/splits.yaml`: `m5_mine` / `m5_validate` / `m5_test`, fixed before any
  5-minute result was inspected.
- `scripts/audit_execution.py` — checks a configured run on its real data for
  fill timing, fill prices inside the traded range, adverse spread, fills on
  bars with no print, and end-to-end look-ahead (rewrite every bar after a cut,
  confirm earlier fills are identical). Exits non-zero, so it can gate a loop.
- `qtrader.analysis.attribution` — `diagnose_shortfall` attributes a run's
  result to **signal**, **holding period**, **frequency** or **cost** by
  arithmetic on `net = n * (gross - cost)`, plus `performance_summary` with the
  long/short split.
- `qtrader.experiments.ledger` and `scripts/search.py` — one command per
  hypothesis, recording label, hypothesis, overrides and outcome. Failures are
  recorded too: the count of trials against a window is the multiple-testing
  burden for anything later found on it.
- `CrossSectionalResidualStrategy.allow_long` and `max_hold_intervals`;
  `BarPanel.replace_field`; `us_liquid_100` universe.

### Fixed

- **Fills were possible on bars where nothing traded.** The engine gated new
  exposure on the liquidity mask at the *decision* bar while documenting that it
  gated on a print in the *execution* bar. The liquidity mask tolerates
  `max_stale_bars` of silence, so 85 fills in one window landed on silent bars
  against 54 declared stale exits. It now checks `panel.traded` at the execution
  bar and keeps the eligibility check as a separate guard.
- **The pipeline stored in-progress bars.** A bar is only a fact once its
  interval has closed; fetched during market hours the last bar of every
  download was partial (AAPL 18:40: stored close 313.275 on volume 3,264, settled
  313.685 on 7,018). `drop_incomplete_bars` discards them at ingestion. The
  affected sessions were dropped and re-ingested. Found because the raw layer's
  immutability guard fired on a re-download — which is what that guard is for.

### Validation

- `pytest` — 193 passed, including that a bar whose interval has not closed is
  not stored, that no fill is possible on a bar that did not print, and that the
  attribution verdict follows the arithmetic on planted cases.
- **Search concluded: no reliable positive expectancy.** Eight trials on
  `m5_mine` produced a best candidate of +1.69 bps gross per trade (t = +1.78)
  against 2.4-3.0 bps of cost; taken once to `m5_validate` it returned +0.23 bps
  (t = +0.19). `m5_test` was never run — there was no candidate worth spending it
  on. Full reasoning in `docs/research/R04-5min-search.md`.
- `scripts/audit_execution.py` passes all five checks on the real 5-minute data.
- Search findings in `docs/research/R04-5min-search.md`: trend has reliably
  negative IC (t −2.6 to −4.7); reversal has reliably positive IC but 92% of the
  1-bar version is bid-ask bounce that dies at a one-bar lag; the surviving
  12-bar residual reversal is one-sided (short leg +3.77 bps t=+2.91, long leg
  +0.85 t=+0.86) and worth ~+1.69 bps per trade against 2.4–3.0 bps of cost.

## 2026-08-26 (intraday volatility seasonality)

### Added

- `qtrader.features.seasonality` — the intraday volatility profile.
  `intraday_volatility_profile` fits a per-minute-of-session multiplier from
  **completed prior sessions only** (expanding mean, shifted by one, flat until
  10 sessions have accumulated); `seasonal_volatility` multiplies a rolling
  level estimate by that shape.
- `TrendRatchetStrategy.seasonal_volatility_profile` (default on).

### Fixed

- **A flat sigma understated opening volatility by three to four times.** Pooled
  over 22 names and 142 sessions, the per-minute standard deviation runs 4.11x
  the session mean at minute 1, 2.62x at minute 5, 2.12x at minute 15 and 0.75x
  by midday — the familiar U. Every threshold built on sigma inherited the
  error, so a nominal 2.5-sigma trend gate was really asking for 0.85 sigma at
  minute 3. Found by looking at the failure gallery: twelve of thirteen losing
  trades in a test week were entered between 09:32 and 09:46.
- `viz.episodes.episode_gallery` raised `ValueError` for galleries taller than
  about eleven rows, because Plotly caps subplot spacing at `1/(rows-1)`. A
  gallery of *every* losing trade is the main reason to ask for one, so this hit
  the intended use. Regression test added.
- `scripts/analyze_episodes.py` no longer forces `--split mine`; omitting it
  uses the config's own date range.

### Validation

- `pytest` — 182 passed. The seasonality tests pin the two things that matter:
  the profile recovers an injected shape, and it is **causal** — tampering with
  later sessions leaves earlier values byte-identical.
- On `mine` the fix halves opening entries (63.7% → 32.4% of trades in the first
  15 minutes), raises the hit rate 36.6% → 39.7% and lengthens holds 93 → 147
  bars. Gross per trade moves +0.45 → −0.86 bps, both inside t = ±0.3.
  **This is a correctness fix, not a performance one:** the old sigma was
  measurably wrong and is now measurably less wrong.

## 2026-08-26 (two-speed entry, tested and shipped off)

### Added

- `TrendRatchetStrategy.session_confirm_z` — gate a fast trigger on the day's
  own drift, so an entry can happen early in a move while still requiring the
  session to be trending in that direction. Off by default.

### Validation

- `pytest` — 175 passed, including that a fast signal firing against the day's
  drift is blocked while one the day agrees with is not.
- On BHVN's session it enters at 10:09 @ 14.53 rather than 12:17 @ 16.16,
  capturing +15.1% of the subsequent move against +3.5%. Across 82 sessions it
  changes nothing (gross/trade +0.17 vs +0.45, t +0.08 vs +0.17), so the default
  is unchanged.
- The decisive-crossing gate was re-tested under the state entry and is still
  flat: +0.45 / −0.03 / −0.07 / −1.61 / +0.25 / −0.64 bps as the threshold rises.
- Recorded in `docs/research/R03` §8, with a note that the strategy now carries
  21 parameters and should be pruned.

## 2026-08-26 (trend_ratchet redesigned around a state entry)

### Added

- `qtrader.features.trend.session_drift_zscore` — drift since the session open,
  `z = sum(r) / (sigma * sqrt(n))`. Standard normal under a driftless random
  walk with no window and no parameter, and the only estimator whose window
  grows with the day, so it can resolve a slow sustained move that every fixed
  span reads as noise. Now the default `trend_estimator`.
- `trend_z_reset` — the hysteresis band. After an exit a symbol is disarmed
  until `|z|` cools below it, and an open position is released when `z` reaches
  it on the opposite side.

### Changed

- **Entry is now a state, not an event.** The old rule needed a MACD crossing
  and a significant trend on the same bar; on the two sessions that prompted
  this, that conjunction fired 0 times out of 30 crossings. The conditions are
  now asked every bar: significant drift, oscillator on the same side,
  optionally still accelerating (`min_cross_zscore`).
- `exit_on_opposite_cross` defaults to **false** and the configs were updated to
  match. It had been closing 81% of positions and capping holds at ~10 bars. The
  trend statistic changing sides replaces it.
- Removed `allow_continuation` / `allow_reversal`: they were modes of the
  crossing trigger and have no meaning for a state entry.

### Validation

- `pytest` — 172 passed. New tests: a sustained trend is entered without waiting
  for a crossing; a position survives histogram flips while the trend holds;
  hysteresis prevents re-entry until the trend has lapsed; the trend statistic
  releases a position when it changes sides; `session_drift_zscore` is standard
  normal at every bar.
- Trade anatomy on `mine`, before → after: winners +26.9 bps held 26 bars →
  **+85.5 bps held 173 bars**; losers −19.0/13 → −48.6/46; payoff ratio
  1.42 → **1.76**; breakeven hit rate 41.3% → **36.3%** against an actual
  39.3% → **36.6%**. Gross per trade −0.90 → **+0.45 bps**.
- The gross edge remains statistically absent (t = +0.17) and costs are 3.0 bps
  per round trip, so `mine` is still −4.16% net. Written up in
  `docs/research/R03-state-entry.md`.

## 2026-08-26 (selective intraday watchlist)

### Added

- BHVN joins the intraday watchlist (`config/universe/intraday_watchlist.yaml`,
  renamed from `intraday_trio`).

### Changed

- `config/backtest/trend_ratchet_intraday.yaml` now runs the EWMA estimator with
  both gates engaged (`trend_z_min: 2.5`, `min_cross_zscore: 2.0`). The EWMA
  choice is deliberate and local to this config: an intraday watchlist wants an
  opinion during the whole session, the open included, and the two gates are
  what hold the frequency down. On `mine` this pair takes **0.8 trades a day**
  per 22 names against 3.0 for the ungated window estimator.

### Validation

- Frequency/selectivity map on `mine`, per-trade gross return and its
  t-statistic:

  | estimator | z_min | cross gate | trades/day | gross/trade | t | win |
  | --- | --- | --- | --- | --- | --- | --- |
  | window | 2.5 | off | 3.0 | +3.00 bps | +1.33 | 45.9% |
  | window | 2.5 | 2.0 | 0.8 | +5.85 | +1.10 | 50.8% |
  | window | 2.5 | 3.0 | 0.2 | +19.22 | +1.76 | 64.3% |
  | ewma | 2.5 | 2.0 | 9.2 | +1.26 | +0.56 | 40.3% |

  Tightening raises the win rate and cuts cost exposure, but **no cell reaches a
  t-statistic of 2**, and the most selective one rests on 14 trades in four
  months. Selectivity buys a cheaper book, not a measurable edge.
- Today (2026-08-26, 09:30–13:08 NY, 4 names): 4 round trips, all before 09:53.
  USAR long 09:32 @ 19.61 → 09:45 @ 20.17, exiting within 2 bars of the day's
  high. BHVN short 09:32 @ 13.90 → 09:39 @ 13.47.
- BHVN rose 18.5% intraday and was never bought. Its per-bar sigma is 55 bps
  against PLTR's 14, so the whole move is 2.27 sigma-root-n — and at every
  golden cross the trend z was 0.4–1.71, under the 2.5 gate. The statistic is
  measuring the move against the stock's own noise, which is what it is for.

## 2026-08-26 (statistics that need less history)

### Added

- `qtrader.features.trend.ewma_drift_zscore` — the drift z-score without a fixed
  window. Exponential weights with a closed-form `S2 = sum beta^(2k)`, so the
  statistic is standard normal under a driftless random walk at **every** bar
  including the session's second, and demands a larger move when it has seen
  fewer returns. Removes the hour-long blackout at the open that
  `drift_zscore` imposes.
- `qtrader.features.momentum` — `session_macd` (EMAs restarted each session, so
  an overnight gap cannot appear as a spurious spike) and `macd_cross_zscore`,
  the calibrated form of "how sharply did it cross": the histogram's one-bar
  change divided by `sigma_bar * P * ||g||`, where `g` is the filter's impulse
  response to a return shock. Scale-free across price levels, and `||g||` is
  tabulated per within-session position because a restarted filter is not a
  converged one.
- `TrendRatchetStrategy` gains `trend_estimator` (`window` / `ewma`),
  `trend_span`, and `min_cross_zscore`.

### Changed

- `TrendRatchetStrategy` now computes its MACD per session rather than across
  the whole series, so no overnight gap enters the trigger. Effect on the
  baseline is small (242 trades vs 245, +3.00 vs +2.86 bps per trade).
- `trend_estimator` defaults to `window` and `min_cross_zscore` to 0 (off).
  Both defaults rest on a cost argument, not a performance search: see below.

### Validation

- `pytest` — 168 passed, including simulation checks that both new statistics
  are standard normal at every bar under a driftless random walk, that the
  impulse-response norm matches the spread the filter actually produces, that
  the crossing z-score ranks a sharp turn above a gentle one and is identical
  for a $20 and a $600 stock, and that a session restart does not let one
  session's drift colour the next one's opening reading.
- On `mine`: `ewma` removes the blackout but admits 1,104 trades against 242 and
  loses 2.0 bps per trade instead of breaking even — the blackout had been
  filtering out bad trades. The crossing gate at |z| ≥ 2 raises the hit rate
  45.9% → 50.8% and gross/trade +3.00 → +5.85, but the per-trade t-statistic
  falls 1.33 → 1.10, so it is discarding trades faster than it improves them.
- Neither is confirmed out of sample; `validate` was already spent on this
  strategy. Written up in `docs/research/R02` §7.

## 2026-08-26 (charts for any strategy; single-session run)

### Added

- `config/universe/intraday_trio.yaml` (later renamed
  `intraday_watchlist.yaml`) and `config/backtest/trend_ratchet_intraday.yaml` — a single-session run of
  `trend_ratchet` on USAR, ZM and PLTR. The liquidity gate is $5k rather than
  the large-cap universe's $20k, because IEX prints a much smaller share of the
  tape for a name like USAR (median 60-bar IEX dollar volume ~$12k); the
  mega-cap gate would have excluded it and turned a three-name test into a
  two-name one without saying so.

### Changed

- `report.max_candles` in the run config limits how many bars each K-line chart
  draws, counted back from the end, so a run can load warm-up history without
  the chart being buried in it.
- The intraday config now loads a week of prior sessions. The volatility
  estimate and the liquidity filter legitimately span sessions (they read
  within-session returns, so no overnight gap enters them) and loading a single
  day left both blind until 10:29 for no reason. The trend test still needs
  `trend_window` bars of *same-session* history, which is deliberate and is why
  no entry is possible in the first hour.
- `viz.charts.price_chart` now has a fixed four-row layout — price, volume,
  MACD, strategy panel — instead of one lower panel that showed whichever of
  MACD/score the strategy happened to publish. **The MACD row is always
  present**: the strategy's own series when it publishes them, otherwise a
  standard MACD(12,26,9) computed in the chart and labelled as a reference. Any
  strategy therefore produces a chart with K-lines, buy/sell markers, MACD and
  its own view, with no per-strategy chart code.
- `TrendRatchetStrategy` publishes `macd` and `macd_signal` alongside
  `macd_hist`; it already computed them, and the two lines are what a reader
  needs to see why a cross happened.

### Validation

- `pytest` — 150 passed, including: every chart has a MACD panel even for a
  strategy that never mentions MACD, the reference label distinguishes it from
  a strategy's own, and a strategy publishing only a histogram does not have
  MACD lines invented for it.
- Trend-window sweep on `mine` (82 sessions): 15 → −0.03%, 20 → −1.47%,
  30 → −4.02%, 45 → −1.04%, 60 → +0.08%, 90 → −1.50%. No systematic gain from a
  faster window, so the first-hour blackout is not costing measurable return.
- Today's session (2026-08-26, 09:30–12:25 NY, 176 bars): at the configured
  `trend_z_min = 2.5` the strategy took **no trades**. Max |z| all session was
  1.69, and at a MACD cross only 1.50 (USAR) / 0.90 (ZM) / 0.59 (PLTR) — PLTR's
  +2.9% and USAR's −3.0% are not distinguishable from a random walk of their own
  volatility over a 60-bar window. Charts verified with a demonstration run at
  `trend_z_min = 0.5`.

## 2026-08-26 (trend-ratchet strategy)

### Added

- `qtrader.features.trend` — `rolling_slope` (OLS drift per bar, computed from
  rolling moments so it is O(1) per bar and exact) and `drift_zscore`, which
  standardises the slope against its sampling distribution under a **driftless
  random walk**: `z = b / (sigma * scale(W))` with `scale(W)^2 = sum_k
  (sum_{i>=k} w_i)^2`. Verified by simulation to be standard normal under that
  null (sd 1.013, P(|z| >= 1.96) = 5.3%).
- `qtrader.strategies.trend_ratchet.TrendRatchetStrategy` — MACD-histogram sign
  change as the trigger, `|drift_zscore| >= trend_z_min` as the authorisation,
  continuation and reversal separately switchable, fixed-fractional risk sizing,
  and a monotone exit barrier `max(entry - s*sigma_H, extreme - r*sigma_H)` that
  caps the initial loss at one risk unit and ratchets up behind a winner.
  Path-dependent, so it runs its own bar loop (ADR-0005).
- `config/backtest/trend_ratchet.yaml`; `docs/research/R02-trend-ratchet.md`.
- Tests: the rolling slope matches an explicit regression; the closed-form slope
  scale matches simulated random walks; the z-score is standard normal on
  driftless walks and detects real drift; the barrier stops an immediate adverse
  move, holds a winner, takes profit on a retracement, and a wider trail holds
  what a tighter one exits; risk sizing halves the weight when volatility
  doubles; the book respects its position limit.

### Fixed

- The first draft used the regression's own t-statistic as the trend filter. Its
  standard error assumes independent residuals, which prices violate: on a
  driftless random walk it exceeds 2 about **80%** of the time, so the filter
  admitted almost everything. Caught by a test asserting the null distribution.
  Replaced with the correctly scaled `drift_zscore`.
- The price chart's lower panel assumed a strategy exposing `macd_hist` also
  exposed `macd` and `macd_signal`, and raised `KeyError` otherwise.

### Validation

- `pytest` — 148 passed.
- `mine` (82 sessions): +0.08% net, 245 trades, hit rate 41.2%, turnover 0.78x.
  `validate` (38 sessions): −0.40% net, 82 trades, hit rate 37.8%.
- The risk geometry behaves as specified: winners +26.9 bps held 26 bars
  (mean MAE −8.0), losers −19.0 bps held 13 bars — payoff ratio 1.42, breakeven
  hit rate 41.3% against an actual 39.3%.
- The `abs_trend_zscore` gradient found on `mine` (t = +2.46 against a bar of
  2.99) did **not** replicate on `validate`. `trend_z_min = 2.5` was
  pre-committed as the middle of the tested range before validation was run.

## 2026-08-26 (reusable win/loss diagnosis)

### Added

- `qtrader.analysis.diagnose(config, split=...)` — one call from a config to a
  finished diagnosis. Returns a `Diagnosis` with `.conditions`, `.contrast`,
  `.survivors`, `.threshold`, `.summary()`, `.save()` and `.write_report()`.
  Accepts a `RunConfig` or a path, optional split and parameter overrides, and
  can reuse an already-executed `Run`.
- `qtrader.analysis.features` — `market_features` builds a universal,
  strategy-agnostic screen (volatility, residual volatility, beta, trailing
  return, relative volume, bar range, VWAP distance, sector return, time of day,
  breadth, market state, dispersion); `FeatureSet` merges it with a strategy's
  own view and samples one symbol at one bar.
- `Strategy.setup_features(signals, context)` — optional hook for a strategy to
  expose its own setup quantities to post-trade screening. Documented to require
  scale-free, causal values. Implemented for `cross_sectional_residual` (score,
  |score|, residual signal, beta, move-in-vols) and `ma_cross` (MA gap and MACD
  histogram, both in bps of price rather than as price levels).
- `StrategySignals.stack(column)` — per-symbol indicator frames to one wide frame.
- `Episodes.setup_columns` plus `.screen()`, `.contrast()`, `.profile()` and
  `.threshold()`; column roles persist in `episodes.json` alongside the parquet.

### Changed

- Episode extraction no longer hard-codes a feature list or the cross-sectional
  strategy's indicator names; features are discovered from the run. The screen
  grew from 16 to 19 features for `cross_sectional_residual`, and degenerate
  features (e.g. breadth on a one-symbol universe) drop out on their own.
- Episode K-line windows now carry every indicator the strategy produced, not
  just `score`.
- `scripts/analyze_episodes.py` is a thin CLI over `diagnose`.

### Validation

- `pytest` — 120 passed, including a test that the identical call diagnoses
  `ma_cross` and `cross_sectional_residual` with no analysis-side change, that a
  strategy declaring nothing is still screened against the universal features,
  that a strategy feature wins a name clash while the universal one is kept, and
  that universal features are causal.
- Verified by hand on `ma_cross` (`--split burned`): 15 features screened
  including its own `ma_gap_bps` and `macd_hist_bps`, with the cross-sectional
  features correctly dropping out on a one-symbol universe.
- All four `xsec_reversion` analyses regenerated under the wider screen; the
  R01 conclusion is unchanged (nothing clears |t| = 3.01 on any of them).

## 2026-08-26 (post-trade episode analysis)

### Added

- `qtrader.analysis` — trade **episodes**: one per round trip, holding the
  K-line window from 60 bars before the entry to the exit, sixteen setup
  features read at the *decision* bar, and the outcome as gross / net / cost /
  MFE / MAE. `Episodes.save/load` persists both tables as parquet.
  `oriented_paths` and `excursion_summary` describe how outcomes were reached.
- `qtrader.analysis.conditions` — feature screening against the outcome
  (Spearman, quantile profiles, monotonicity), winner-vs-loser contrast, and
  `bonferroni_t_threshold`, reported next to every screen.
- `qtrader.experiments.splits` + `config/splits.yaml` — named contiguous
  evaluation windows carrying a purpose string (ADR-0004): `mine`, `validate`,
  and `burned` for the window that chose the current parameters.
- `qtrader.viz.episodes` — mean oriented path chart (winners vs losers, with the
  surviving-episode count and truncation at 20% survival), and a best/worst
  candlestick gallery normalised to bps-from-entry with shorts flipped so up is
  profit. `write_episode_report` assembles the analysis into one HTML file.
- `scripts/analyze_episodes.py`; `--split` and `--set` on `run_backtest.py`,
  `--split` on `sweep.py`; `RunConfig.with_overrides`.
- `CrossSectionalResidualStrategy.max_abs_zscore` — an upper conviction bound,
  set to 1.5 in the shipped config and documented as a band-limited hypothesis
  rather than validated alpha.
- Tests: screening must find a planted effect and reject noise; episode features
  must be read at the decision bar; excursions must bracket the realised return;
  episodes must round-trip through disk (103 total).

### Changed

- Integration fixtures moved to `tests/integration/conftest.py` so the pipeline
  and episode tests share one synthetic store.
- `viz.report._CSS` is now the public `REPORT_CSS`, shared with the episode
  report.

### Validation

- `pytest` — 103 passed.
- Episode analysis over `mine` (1,766 round trips) and `validate` (751):
  no feature clears the Bonferroni threshold of |t| = 2.96. Winners and losers
  have statistically identical pre-entry paths; the mean post-entry path is flat.
- The `abs_score` gradient found on `mine` did not replicate on `validate`;
  the conviction cap's *ordering* did (uncapped worst in both windows) but its
  level did not, and the best validation result is break-even.
- Findings written up in `docs/research/R01-conviction-and-reversion.md`.

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
