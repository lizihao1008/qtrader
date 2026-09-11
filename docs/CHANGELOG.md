# Changelog

## 2026-09-11 (regime thresholds moved onto the random-walk null)

### Changed

- `slope_z` is now the Kalman slope over its sampling scale **under a driftless
  random walk**, not over the model posterior `sqrt(P[1,1])`. The filter carries
  a 3x3 Lyapunov recursion `V <- A V A' + s^2 b b'` for `z = (level, slope, y)`
  alongside `P`; `LocalLinearTrendFilter.step` returns a `FilterStep` with both
  `slope_std` (model) and `slope_rw_std` (null), and takes a separate
  `null_var` so a volatility profile can later change the null without touching
  the filter's `R`.
- Kaufman ER gates are in random-walk units: `er_rw = ER_n * sqrt(n)`, mean 1.0
  under the null for every `n`. `er_min_entry` / `er_min_hold` are replaced by
  `er_entry_rw` / `er_hold_rw` / `er_flip_rw`; the snapshot carries `er_rw`,
  `signed_er_rw`, `er_bars`.
- `exit_z` is replaced by `exit_margin` (>= 0): UP leaves when
  `slope_z < -exit_margin`, DOWN when `slope_z > +exit_margin`. Larger is
  stickier. The old knob read the other way round.
- The weaken path can no longer jump straight to the opposite regime — it only
  returns to FLAT, and `reversal_h` / `reversal_z` is the one direct UP<->DOWN
  route. The weaken flag is released when `slope_z` recovers instead of
  latching for the rest of the leg.
- Presets re-derived as null quantiles and quoted by their false-entry rate on
  simulated driftless random walks: sensitive 7.8, balanced 3.0, conservative
  0.8 entries per session.
- `plot_regime(..., config=cfg)` takes its reference lines from the config that
  produced the replay.
- `run()` computes session dates once for the frame instead of rebuilding a
  `DatetimeIndex` per bar (was 25% of runtime).

### Added

- `regime.evaluate.null_entry_rate` — replays the detector over simulated
  driftless random walks, where every entry is false by construction.
- `regime.evaluate.describe_entries` — signed move around each entry in
  random-walk units, splitting "the move it is describing" (negative offsets)
  from "a forecast claim" (positive offsets).

### Fixed

- `evaluate_regime` raises on an empty event table instead of returning a frame
  of NaNs. On IEX, QQQ drops ~4 minutes a session, so `detect_trend_events`
  could silently score zero bars and a whole delay/FAR grid came back NaN.

### Removed

- `er_min_entry`, `er_min_hold`, `exit_z`, and the `er` / `signed_er` snapshot
  columns.

### Validation

- 34 tests in `tests/unit/test_kalman_cusum.py` (537 repo-wide, all passing),
  including: `slope_rw_std` against Monte Carlo; `slope_z` standard normal on
  simulated random walks (sd 1.00 +/- 0.15, `P(|z|>2)` 0.046 +/- 0.03);
  `E[ER_n*sqrt(n)] = 1` at n = 5, 10, 20, 40; a null false-entry ceiling per
  preset; the weaken flag releasing; the weaken path never reaching the
  opposite state; `exit_margin` monotone in stickiness.
- Measured on AAPL/NVDA/TSLA/QQQ/JPM, 2026-06-01 -> 2026-08-01, balanced:
  flips/session 57.6 -> 5.4, mean trend duration 5.0 -> 9.2 bars,
  `slope_z` sd 1.44-2.03 -> 1.03-1.07 across symbols.
  At entry the previous 10 bars have moved +1.78 sigma in the declared
  direction (100% of entries); the following 5-30 bars move +/-0.04 sigma with
  `frac_right_way` 0.46-0.52 — the state describes, it does not forecast.

## 2026-09-11 (regime-anchored ER and weaken-and-tighten)

### Changed

- After FLAT→UP/DOWN (or a hard reversal), Kaufman ER no longer mixes bars from
  before the current regime. Kalman slope is *not* reset. If `slope_z` fades
  below `weaken_z` or `weaken_frac` of the post-entry peak, the ER lookback
  shrinks to `er_tighten_window`. A recent against-path with `er >=
  er_min_hold` flips to the opposite trend; a messy window (`er <
  er_min_hold`) goes FLAT. Snapshot adds `signed_er` and `weakened`. Disable
  with `anchor_er_to_regime=False`.

### Validation

- Existing Kalman–CUSUM tests plus: in-trend ER ignores the pre-trend path;
  a faded UP plus a recent down path flips DOWN even when `exit_z` /
  `reversal_h` cannot fire; a faded UP plus chop goes FLAT; a single adverse
  bar still does not exit.

## 2026-09-10 (Kalman–CUSUM online regime detector)

### Added

- `qtrader.regime` — first regime-layer module (ADR-0009).
  `KalmanCUSUMRegimeDetector.update(bar)` / `.run(df)`: session-anchored
  local-linear-trend Kalman filter → `slope_z` (or vol-normalized slope) →
  two-sided CUSUM → ER gate → FLAT/UP/DOWN with hysteresis and hard reversal.
  Strictly causal; no smoother. Presets `sensitive` / `balanced` /
  `conservative` are initial defaults, not a fit.
- `qtrader.regime.evaluate` — detection delay, false alarms, flip rate,
  capture ratio, mean regime duration. Labels may look ahead; they do not
  enter the filter. `delay_far_grid` sweeps a parameter cell onto that curve.
- `qtrader.viz.regime.plot_regime` / `plot_delay_far` (plotly; same
  candlestick / range-break helpers as `price_chart`).
- `replay_symbol` loads via `load_clean_bars` (same clean 1-minute store).

### Changed

- Regime charts no longer use matplotlib. `plot_regime` follows
  `price_chart` (candles, volume, exchange-local range-breaks) and writes
  HTML if a path is given.

### Validation

- 18 unit tests: no-lookahead (future prices cannot move past states),
  `run` equals sequential `update`, noiseless slope recovery, CUSUM
  non-negative, conservative chop stays FLAT, one-bar dip does not exit UP,
  UP→DOWN reversal skips FLAT, overnight gap does not fire the next open,
  planted-trend delay, plotly layout smoke.
- QQQ IEX 1-minute smoke (2026-08-17 → 2026-09-08, 16 sessions, 8
  `trend_events` labels): balanced mean delay 8.6 bars, capture 8/8,
  ~16 labelled-FAR/session (the label is a strict 30-minute trend; most
  detector entries are shorter moves). Flip rate and duration are the honest
  twitchiness scores: balanced ~32 flips/session, mean trend run ~11 bars.
  Charts: `results/kalman_cusum/`.

## 2026-09-10 (audit of the trend-precursor test; a second design)

### Added

- `experiments/trend_lift.py` — two designs with no control pool:
  `unconditional_lift` (every bar is a sample, the base rate is the real one)
  and `hard_negative_contrast` (among bars that already look like a breakout,
  what separates the runners). Six tests, including a **planted-signal control**
  (a synthetic precursor must be recovered at AUC > 0.9, so a null means
  something) and a pure-noise control.
- `docs/research/R33-trend-precursor-audit.md`.

### Findings

- **The labelling is clean.** `sigma` uses `<= t`, everything else the open
  interval `(t, t+H]`, the factor ends at or before `close[t-1]`, and the
  bootstrap resamples by session. Factor and label share no bar.
- **The defect is the control pool, not the labels.** `_eligible_controls`
  removes every bar within 30 minutes of *any* event, so controls are drawn by
  construction from the quiet parts of the day — selection on the outcome. It
  did not create a false positive: the existing run is already null (median
  directional AUC **0.424**, median top-decile lift **0.82**).
- **Comparing against random times would be worse**, not better: unmatched
  controls mostly measure "trends happen when the market is active".
- **Unconditional design**: 106,629 bars, 187 events, base rate **1 in 570**.
  AUC 0.448-0.509 (median 0.478); top-decile lift median **0.88**.
- **Nothing is elevated before a Trend Start.** |mom_5|, realised vol, bar
  range, vol ratio and volume ratio all have event/other ratios of **0.91-0.97**
  — five unrelated families all pointing slightly the *other* way.
- **Hard negatives**: bars in the top decile of |mom_5| start a trend at
  **0.76x** the base rate, and stacking volatility filters takes it to 0.36x.
  A breakout-looking bar is *less* likely to start a trend than a random one.
- **The anchor hypothesis was tested and rejected**: `candidate_run_length` has
  median 1 (p90 3), and relaxing the target to "starts within the next K bars"
  leaves every AUC in 0.45-0.53 for K up to 20.
- **The binding constraint is power.** At 187 events the smallest detectable AUC
  is **0.572**; at 4,000 it is 0.516. One symbol cannot answer this question.

### Next

- Run the collector over the full 22-symbol universe (one config change) before
  refining anything else.
- Test compression rather than momentum: `P(trend start | inside a tracked
  consolidation box, close near an edge)` against the 1-in-570 base rate, using
  the causal detector already wired in via `experiments/consolidation_gate.py`.

### Validation

- 477 unit tests pass; 6 new. Nothing in `trend_precursor.py` or `labels/` was
  changed — this is an audit plus a second design, not a replacement.

## 2026-09-10 (trend precursor test)

### Added
- `qtrader.features.lookback.log_momentum`: `log(close_t / close_{t-N})` only
  when the lookback is an exact same-session clock span.
- `experiments/trend_precursor.py`: 5 TOD-matched controls per Trend Start,
  directional momentum, Cohen's d / rank corr / AUC, decile lift, day-block
  bootstrap (500), event-time and AUC-heatmap PNGs. Not a model, not a search.
- Notebook: `notebooks/exploratory_only/trend_precursor.ipynb`.

### Validation
- Unit tests: future closes do not move `mom_N`; overnight gap is not a
  5-minute return; event-bar close is not in any factor; controls sit outside
  the 30-minute exclusion window; seed 42 is deterministic.

## 2026-09-09l (trend charts stay on the event's session)

### Fixed
- Trend audit charts clip the candle window to the event's session. A 15:20
  event no longer pulls Monday's open onto the axis (title Sep 4, x-axis
  Sep 7–8).

### Validation
- Unit test: Friday 15:20 chart x-values are all 2026-09-04.

## 2026-09-09k (opening reversal is more than the first close)

### Changed
- `require_first_bar_aligned` now rejects a path that goes against `close_t`
  by more than it has already led. A one-tick first close then a dip through
  the start (QQQ 2026-08-04 12:38) is no longer an UP event.
- Audit charts put the start line at bar *end* (`trend_start`), so the
  start candle itself stays in the grey pre-window.

### Validation
- Unit test: a +1 tick lead then a larger dip is a candidate only with the
  flag off.

## 2026-09-09j (no initial reversal on trend labels)

### Changed
- Trend candidates now require the first future bar to already move in the
  labelled direction (`require_first_bar_aligned`, default on). A DOWN
  window may not open with an up bar (QQQ 2026-08-20 13:45). Later MAE
  inside the window is unchanged.

### Validation
- Unit tests: a bounce-then-dump bar is a candidate only with the flag off;
  every remaining candidate has `sign(first_return) == direction`.

## 2026-09-09i (trend cooldown from run end; chart header)

### Changed
- Same-direction cooldown now starts after the **last** candidate in a run,
  not the Trend Start. One slow grind is no longer re-sliced every H minutes
  (QQQ 2026-08-04 had 12:38 then 13:10 on the same climb).
- Trend audit charts drop the Plotly legend, give the two-line title more
  top margin, and pin `start` / `t+H` labels to the price axis.

### Validation
- New unit test: a same-direction candidate 17 bars after an 8-bar run is
  not a second event when cooldown is 20.

## 2026-09-09h (minute-level trend event detector)

### Added
- `qtrader.labels.trend_events`: V1 heuristic labels for a clean H-minute
  trend (`ZTrend`, ER, MAE/MFE). `sigma_t` is causal and does not cross a
  session or a missing minute. Consecutive candidates collapse to one Trend
  Start; opposite-direction conflicts are kept and flagged.
- `experiments/trend_collect.py` + `viz/trend_events.py`: tables, summary,
  strongest/random/borderline HTML, overview page.
- `notebooks/exploratory_only/trend_collect.ipynb` is the thin caller.

### Validation
- 10 unit tests: monotone up/down, chop rejected, future prices do not move
  `sigma_t`, a candidate run is one event, overnight gap is not a 1-minute
  return, charts mark start and t+H.
- Smoke: QQQ 2026-09-01..05, 29 candidates → 2 events, artifacts written.

## 2026-09-09g (index_momentum was scratching)

### Changed
- `index_momentum` no longer time-stops. Entry 1.5 / exit 0.0, fade only after
  10 bars, 15-bar re-entry cooldown. QQQ 2026-09-03 had 13 round trips
  (median hold 10 min, two 1–2 minute scratches) because score std is ~1,
  the old 1.0/0.3 band was narrower than the noise, and flatten allowed
  the next bar back in.

### Validation
- 13 unit tests pass, including: a clean rally is one hold; a time-stop plus
  cooldown leaves an 8-bar flat gap.
- Smoke: QQQ 2026-09-03, 13 trades → 4 (holds 62/3/25/93 min). The remaining
  3-minute round trip is the ATR stop, which is allowed to fire before
  `min_holding_bars`.

## 2026-09-09f (session return on the factor chart)

### Added
- `price_chart(..., return_panel=True)` draws a last row: this symbol's
  buy-and-hold return vs the strategy's net return, both rebased to the
  charted window. Off by default. `factor.ipynb` turns it on.

### Validation
- `test_return_panel_sits_below_the_score`: extra y4, score y5, return y6.

## 2026-09-09e (index own-path factors)

### Added
- `index_momentum`: six directional factors from the index's own path
  (`session_z`, `ewma_z`, `ret_5_z`, `ret_15_z`, `vwap_z`, `macd_z`), averaged
  by `combine_score` with no cross-sectional z-score. `rvol` is a gate, not a
  vote. Config `config/backtest/index_momentum_1min.yaml` on `broad_index`.
- `factor.ipynb` now loads that config so a single index still has a finite
  score.

### Why
- A cross-sectional z-score is undefined at N=1 and, on two index ETFs, removes
  the market-common move the strategy is trying to trade.

### Validation
- 11 unit tests: truncation / future-perturbation invariance; a peer's path
  cannot move this name's score; N=1 still has a finite score; VWAP term is on
  the same scale as `session_z`.
- Smoke: SessionLab QQQ 2026-09-01, 390 bars, `score` count 389, std 1.06
  (not dominated by VWAP), 14 trades.

## 2026-09-09d (z-scores were NaN because the book was stale)

### Fixed
- `ensure_bars` treated any overlap with the warmup window as coverage. On
  2026-09-01 only QQQ/SPY/EWJ had been gap-filled; the 22 names still ended
  2026-08-26. Cross-sectional N=1, so every z-score was NaN (`std` of one
  point). It now requires the **target session** on every name and tail-fills.
- `gross_bps` / `hit_rate` are still NaN when that symbol had zero trades
  that day — that is the summary of an empty book, not a factor bug.

### Validation
- 22 ingest/session_lab tests pass.
- QQQ 2026-09-01 after the fill: median eligible N=14, `z_ret_1m` count 389.

## 2026-09-09c (SessionLab downloads missing bars)

### Added
- `SessionLab` adds a symbol that is not in the config universe, and downloads
  it when the local store does not cover the lab window. The only hard error is
  `UnknownSymbolError` — Alpaca does not list the name.
- `Nikkei` / `N225` / `NKY` resolve to `EWJ` (iShares MSCI Japan). Alpaca has
  no Nikkei 225 listing; `JPXN` (JPX-Nikkei 400) is the name match but IEX
  prints it a few times a day, so it is not used as a minute series.
- Two years of `QQQ` (gap-fill to 2026-09-09) and `EWJ` 1-minute and 5-minute
  bars on IEX.

### Validation
- 29 unit tests on ingest / universe / session_lab.
- Smoke: `SessionLab(..., "Nikkei", "2026-04-08")` → `EWJ`, 78 five-minute bars.
- Store: QQQ 1Min 250,398 bars to 2026-09-09; EWJ 1Min 129,029 bars from 2024-09-09.

## 2026-09-09b (xsec factor session chart)

### Added
- `price_chart(..., extra_panel=)` overlays named indicator columns on a new
  row immediately below MACD. Default reports do not pass it.
- `notebooks/exploratory_only/factor.ipynb` loads any session the same way
  `session_lab` does and charts the eight `xsec_momentum` z-scores against that
  day's bars.

### Validation
- 13 `test_charts` tests pass, including overlay row placement (MACD y3,
  factors y4, score y5).
- Smoke: AAPL 2026-04-08, all eight `z_*` columns published, ~390 bars.

## 2026-09-09 (the negative gross is the sign, not the factor definitions)

### Findings

- **The rule is executing the signal faithfully.** Panel, tradeable window at
  the realised 7-bar median hold: the long gate region (`score > +1.5`) earns
  **-0.37 bps**, the short gate region (`score < -1.5`) earns **+0.74**, so the
  predicted combined gross is **-0.55**. Realised was **-0.56** (LONG -0.08 on
  3,137, SHORT -1.06 on 2,978). Nothing is leaking in the thresholds, exits,
  stop or book.
- **The effect strengthens in the tail, which a badly defined factor does not
  do.** Mean return in the score's own direction by magnitude: |score| 0.5-1.0
  **-0.06 bps**, 1.0-1.5 **-0.25**, 2.0-3.0 **-2.72** — 45x stronger, on
  4,560 observations. The +/-1.5 entry gate selects exactly where the factors
  are most reliably inverted.
- **Flipping the signed weights confirms it in both windows.** Hit rate moves
  0.361 -> 0.546 immediately. Restricted to |score| > 2.0 (the region the panel
  identified) gross turns positive: **+0.53 bps on m5_mine and +0.78 on
  m5_test** — the first positive gross this score has produced, predicted by the
  panel before it was backtested.
- **Still not tradeable**: +0.78 bps against a 3.00 bps round trip, net -2.22.
- A fitted holding period does not survive: `flipped, >2.0, hold 7` reads
  **+1.43 bps on m5_mine and -0.45 on m5_test**. The threshold came from the
  panel and replicates; the hold came from m5_mine's own realised median and
  does not.
- Interpretation: these are **short-horizon price-impact detectors**, not trend
  detectors. `ret_5m` inverts perfectly (Spearman -1.000) because a name that
  just moved relative to its peers has usually absorbed an order. The seven
  price factors correlating 0.45 on average (R31) are seven lenses on the same
  event voting seven times.

### Added

- `docs/research/R32-the-sign-not-the-factors.md`.

### Validation

- 423 unit tests pass; no production code changed (every arm is a config
  override). Nothing promoted: the flipped configuration is a mining-split
  observation that replicated once, and m5_test already carries enough trials
  that a fresh window is needed.

## 2026-09-08g (R31 audit: four real defects in xsec_momentum, fixed)

### Fixed

- **The short rank gate was unreachable.** `cross_sectional_rank` returns
  `rank / N`, whose floor is `1/N`, not 0. The median bar has **9** eligible
  symbols, so the floor was 0.111 against a `rank_short < 0.05` gate, and only
  **0.91%** of bars had N > 20. Realised: **3,382 longs against 84 shorts** — a
  long-biased rule, not a symmetric cross-section. Added
  `ranks.cross_sectional_percentile`, which spans [0, 1] so the two gates mirror.
- **Trailing windows reached into yesterday.** `bar_log_returns` zeroes the
  overnight gap, which hid that a 15-bar window at 09:35 is still built mostly
  from yesterday's last bars: **91.9%** of bars 5-13 of a session carried a
  finite 15-bar return that a session-reset window makes NaN.
  `trailing_return` now takes `restart`, and the strategy passes it.
- **The RVOL numerator did not reset either**; `seasonal_volume_ratio` now
  groups its rolling mean by session.
- **RVOL carried a positive weight in a signed score.** It is unsigned — large
  whether price rose or fell — so it pushed every busy symbol towards "long".
  It is now refused as a weight (`UNSIGNED_FACTORS`) and enters as `min_rvol_z`,
  a gate applied to both sides equally.
- **The ten-minute open blackout was not in force.** `no_entry_before: 09:35`
  was copied from a 5-minute config; on a 1-minute grid it means a 09:36 fill,
  and **246 trades (7.1%)** filled before 09:41. Now 09:40.
- `setup_features` derived its column names from `FACTORS` and failed once a
  factor stopped carrying a weight; it now reads what was actually published.

### Changed

- `score_monotonicity` reports a **`tradeable_*`** column measured from the next
  bar's open alongside `fwd_*` from the decision close. The gap between them is
  not a rounding detail: **58%** of this score's 5-minute decile spread accrues
  before a fill can reach it.

### Findings

- Post-fix, from the decision close the ladder is unchanged (Spearman -0.988 /
  -0.915 / -0.988 at 5/15/30 min). **From the next open it is -0.782 / -0.212 /
  -0.673 with a top-minus-bottom of -0.45 / -0.21 / -0.27 bps** against a
  **3.00 bps** round trip. The reversal is real and roughly a seventh of the toll.
- R30's headline overstated the effect by ~2.4x by measuring from a price no
  order could reach, and described as a symmetric long/short test something that
  took 40 longs for every short.
- **Post-fix backtest (m5_mine).** The book is symmetric and the blackout binds:

  | arm | n | LONG | SHORT | gross | net | return | fills < 09:41 |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | fixed | 6,115 | 3,137 | 2,978 | -0.56 | -3.56 | -33.87% | **0** |
  | fixed + vol-normalised | 5,432 | 2,785 | 2,647 | -0.36 | -3.36 | -28.97% | 0 |

  Worse than the broken version (-19.26%), and expected: the rule now actually
  takes the short side, and the ladder says the sign is inverted, so shorting
  the bottom bucket is the wrong side of a real effect. The audit's
  volatility-normalised gross of +0.51 bps/trade did **not** reproduce here
  (-0.36); the two runs must differ in some other respect and that is worth
  reconciling before either number is quoted.

### Scope correction

- **Cross-sectional standardisation removes whatever the universe does in
  common.** A day on which every name rises leaves this score unmoved by
  construction, so nothing measured here can confirm or refute market-level
  time-series momentum. R30 did not say so; the config now does.

### Validation

- 423 unit tests pass; 7 new pinning each defect: the short gate is reachable at
  N=9, a single eligible symbol has no percentile, both gates admit end to end,
  a trailing window does not cross a session boundary, an unsigned factor cannot
  carry a weight, the volume gate narrows both sides, and the bucket table
  reports the tradeable window.

## 2026-09-08 (audit of `xsec_momentum`)

### Findings

- Added `docs/research/R31-xsec-momentum-audit.md`. The 5% lower percentile gate
  is almost always unreachable because pandas ranks start at `1/N` and the
  effective eligible universe has median `N=9`; the reported book is 3,382
  longs versus 84 shorts, not a symmetric long/short test.
- About 0.75 bps of the headline 1.35 bps five-minute reversal occurs before
  the next-open fill. The executable remainder is about 0.62 bps against a 3.00
  bps round trip.
- Documented further semantic gaps: momentum windows cross sessions near the
  open, unsigned RVOL is added as a long-direction vote, seven price inputs are
  redundant, the nominal 22-name universe is effectively only nine names per
  minute, and the configured strategy violates the ten-minute opening blackout.
- A symmetric-rank diagnostic remains negative gross. The implementation defects
  invalidate the original interpretation but do not conceal a profitable edge.
- Volatility-normalising the return factors turns gross positive (+0.51
  bps/trade with symmetric ranks), but net remains -2.49 bps/trade. This is a
  useful diagnosis of volatility contamination, not a tradeable result.

## 2026-09-08f (cross-sectional intraday momentum score)

### Added

- `strategies/xsec_momentum.py` — eight factors per symbol per minute
  (`ret_1m/5m/15m/30m`, `vwap_deviation`, `relative_ret_15m`, `rvol_5m`,
  `er_15m`), each cross-sectionally z-scored, weighted into one score, and
  ranked cross-sectionally. Entry needs score AND rank extreme; exits are the
  score fading, an ATR stop, a holding limit, or the session close. A reversal
  closes without flipping on the same bar, and nothing is held overnight.
- `features/efficiency.py` — Kaufman efficiency ratio (and a signed variant),
  session-bounded so the overnight gap never lands in the numerator.
- `seasonality.seasonal_volume_ratio` — RVOL against the same minute of
  completed prior sessions, so the intraday volume U-shape is not read as
  signal.
- `features/score.py` — weighted cross-sectional combination that skips missing
  factors rather than scoring them as average, with a `min_factors` floor.
- `analysis/score_monotonicity.py` and `scripts/score_monotonicity.py` — bucket
  every (bar, symbol) score against forward 5/15/30-minute returns.
- `config/backtest/xsec_momentum_1min.yaml`.

### Findings

- **The score is strongly monotone and inverted.** Spearman between decile and
  forward return: **-0.988 at 5 min**, -0.818 at 15, -0.855 at 30, on **118,826
  observations per bucket**. Hit rate falls monotonically 0.507 -> 0.477. Top
  minus bottom is **-1.26 bps at 5 min**.
- **Every return factor inverts; the one volume factor does not.** `z_ret_5m`
  Spearman **-1.000** at 5 min; `z_ret_1m` -0.988; `z_relative_ret_15m` -0.988;
  `z_er_15m` -0.806; `z_vwap_deviation` -0.539. `z_rvol_5m` is **+0.285** and
  gone by 15 minutes.
- **Inverting the weights confirms the sign and still does not pay**: hit rate
  0.369 -> **0.539**, but gross only reaches -0.03 bps/trade and the decile
  spread driving it is 1.26 bps against a **3.00 bps** round trip.
- Same conclusion as R04/R05 (intraday momentum wrong-signed, rank IC -0.023),
  now with a ten-rung monotone ladder behind it rather than one correlation.
- **Caveat:** m5_validate produced 16-21 trades against m5_mine's 3,466 — a
  1-minute data coverage gap in that window, not a result.

### Validation

- 416 unit tests pass; 29 new. Leakage first: truncation invariance and future
  perturbation invariance on both the score and the weights; then the factor
  contracts (ER is 1 for a line and 0 for a round trip, signed ER separates a
  fall from a rally, no window crosses a session boundary, RVOL compares like
  minute with like minute), the missing-factor policy, and the state machine
  (rank gate alone can hold the book flat, fade exit, holding limit, no same-bar
  reversal, book capacity, gross <= 1, nothing held into the close, a new
  session starts flat).

## 2026-09-08e (the corrected momentum estimator is worse)

### Findings

- **No fixed-span EWMA beats the `session` estimator.** Wide exit, no hard stop,
  only the estimator varying. m5_mine/m5_validate/m5_test returns:
  `session` **-1.24% / +1.64% / +1.47%**; ewma span 3 -3.55/-3.09/-0.18;
  span 6 -6.68/-1.67/-5.40; span 12 -2.60/-4.16/-3.42; span 24
  -0.34/+0.25/-0.63. The ordering among spans 3-12 is noise; the signal is that
  the growing-window estimator wins and **the best fixed span is the longest**.
- Risk does not improve either: worst trade with ewma span 6 is **-866 bps** on
  m5_validate against -648 for `session`.
- **Coherent with R04/R05**, which measured intraday momentum as reliably
  wrong-signed (rank IC -0.023, t = -11.2). `session` is not a momentum
  estimator — its numerator is `log(close/open)`, i.e. distance from the open —
  so replacing it with a genuine short-horizon read moves the gate toward the
  statistic measured to be inverted. **The name was wrong; the quantity was
  doing useful work under a wrong name.**
- Trade counts move ~1% (2029 -> 2053, 1058 -> 1069) while returns swing 7
  points: the estimator swaps *which* candidates fire at the margin and the
  binding book cascades that (R23 §2). Count is not membership.
- **All three defects found from one chart were load-bearing**: capping the
  gap-inflated stop removes the return (R28), lifting the held-symbol blindness
  does nothing, and correcting the momentum label makes it worse. The apparent
  profit lives in the defects because the signal underneath has no information.

### Findings (continued — the decomposition)

- **Only 57% of the two arms' books are shared** (604 of ~1060 on m5_test).
  Trades taken only by `session` are worth **+5.94 bps**; those taken only by
  `ewma(6)` are worth **-4.91**. The estimator swaps ~450 good trades for ~450
  bad ones — an 11 bps swing on 43% of the book, which is the entire result.
- **All the gross edge is in the longs, and `session`'s longs are 3x better**:
  LONG +10.18 (session) vs +3.46 (ewma6); SHORT -2.67 vs -4.81.
- The reason is what `session` gates on: `z > +0.25` means `log(close/open)` is
  meaningfully positive, i.e. **the stock is up on the day**. The profitable
  component is a day-level relative-strength condition, not momentum in any
  timeframe — and it has been travelling under the name `momentum_z` since R01.

### Added

- `docs/research/R29-the-corrected-momentum-estimator.md`.

### Validation

- 387 unit tests pass; no production code changed (all arms are config
  overrides). No committed configuration changes.

## 2026-09-08d (hard stop, live tracking, and the momentum definition)

### Added

- `SRMomentumStrategy.hard_stop_bps` — a ceiling on the initial loss applied
  **after** the sigma stop and the ATR cap, so it binds whatever they produce
  and no volatility reading can lift it. `min(2*sigma_H, 2*ATR, 150 bps)`.
- `SRMomentumStrategy.track_breaks_while_held` — lifts the `position == FLAT`
  gate in `_track_break`, so the level machine keeps running under an open
  position. Both default to off; no existing result changes.

### Findings

- **`momentum_z` was measuring distance from the session open, not momentum.**
  The config never set `momentum_estimator`, so it defaulted to `session`:
  `z = sum(r since open)/(sigma*sqrt(n))`, whose numerator is `log(close/open)`.
  Its sign is the sign of `close - open` on **78% of bars**. On AMD 2026-06-29
  it read **-1.01 at 11:00** while `ewma(6)` read **+0.69** and had turned
  positive at 10:30 — which is what the chart shows. The EWMA estimator was
  already implemented and simply not selected.
- **The hard stop works precisely.** Worst single trade across the three
  windows: -643/-648/-711 (R25/R26 wide) -> **-237/-232/-245**; trades below
  -200 bps: 95/58/56 -> **6/7/4**, better than the pre-R25 configuration
  (9/12/8). The AMD trade goes -490 -> -198 bps.
- **And it removes the positive return.** m5_test +1.47% -> **-3.71%**. This
  confirms R27 exactly: the wide exit's return WAS the left tail, and there is
  no version of it that is both positive and risk-controlled.
- Capping single-trade losses barely moves the drawdown (-7.7% -> -7.5% on
  m5_test) — the drawdown is an accumulated negative expectation, not a few
  disasters.
- Live tracking raises setups seen on m5_test from 1,497 to 1,764; returns do
  not improve.

### Validation

- 387 unit tests pass; 6 new (the hard stop binds a 314 bps sigma to 150 bps,
  never widens a tighter stop, composes with the ATR cap to the tighter of the
  two, rejects a non-positive value; a held symbol goes blind by default and
  keeps forming breaks with tracking on).

## 2026-09-08c (the AMD short that never stopped — R25's result is short volatility)

### Findings

- **Why the AMD 2026-06-29 short never stopped.** σ_H at the 10:00 decision bar
  is **314 bps** (gap-down open; the same name reads 88-106 bps four hours
  later). With `stop_sigmas: 2.0` and no ATR cap the initial stop sits at
  **544.91 — 32.2 points away, 2.8 points above the session high**. Nothing
  malfunctioned; the geometry did what it was configured to do. With
  `initial_stop_atr: 2.0` restored the same trade exits at 523.0 for **-198 bps
  instead of -490**.
- **Why no long during the rally.** `watched_level` is NaN on every bar after
  the entry: `_track_break` registers breaks only while the symbol is FLAT
  unless `exit_on_opposite_signal` or `allow_add_back` is on. The 29-point rally
  was structurally invisible — the long was never evaluated, not rejected.
  Enabling `exit_on_opposite_signal` does NOT fix it (still -490 bps):
  `momentum_z` stays negative until 13:00 and clears +0.25 only at 13:15, at
  price 537.
- **The result that qualifies R25 and R26.** Over m5_test: the wide exit gives
  gross +3.98 / net +0.98 / return +1.47%, but **worst trade -711 bps and 56
  trades below -200 bps**, against **-273 and 8** for the original tight config.
  Restoring the ATR cap on top of the wide trail is worse than the original
  (-4.40% vs -1.36%), so the return and the fat left tail are the same
  mechanism. At `max_weight: 0.15` a -711 bps trade is ~-1.07% of equity.
- R25 §1's MFE measurement stands; what was missing was the shape of the
  distribution behind the improvement. Same failure mode as R24 from the
  opposite direction: there a mechanism cut the right tail, here it grows the
  left one.

### Added

- `docs/research/R27-the-amd-short-that-never-stopped.md`; R25 and R26 annotated
  in place so neither can be read without it.

### Next

- Cap the volatility estimate itself (σ_H against its own trailing session
  median) rather than capping the stop with a second volatility measure, then
  re-run R25 §3.
- Report `worst trade` and `count < -200 bps` in every future arm table.

## 2026-09-08b (galleries show the consolidation box and the gate readings)

### Added

- `setup_gallery(..., ranges=...)` shades every consolidation box the
  market_state detector was tracking inside a panel's window — one rectangle per
  `range_id`, at the high/low it had frozen, drawn from the detector's own state
  rather than recomputed, so the picture is what the gate actually saw.
- Each panel title now carries the gate readings **at the decision bar**
  (momentum z, relative volume, horizon sigma, acceptance count, VWAP side) —
  what the entry rule tested, one bar before the fill.
- `scripts/plot_setups.py --ranges`. It refuses to run on a non-5-minute panel,
  because the detector's thresholds are stated in 5-minute bars and boxes drawn
  from another series would be a plausible-looking lie.

### Validation

- 381 unit tests pass; 5 new on the boxes (own high/low, one rectangle per
  range_id, a symbol with no range data still renders, omitting `ranges` leaves
  the panel byte-identical) and 1 on the title readings.
- Rendered `results/sr_momentum_ml12__m5_test__trail_sigmas3.5_stop_sigmas2.0_initial_stop_atrNone/setups_wide_exit.html`:
  10 winners and 10 losers over m5_test with the wide-exit geometry, 94 boxes
  drawn across the 20 panels.

## 2026-09-08 (per-symbol exit widths: rejected; the one-parameter version is positive)

### Added

- `stop_sigmas` / `trail_sigmas` accept a `{symbol: width}` mapping as well as a
  float. A mapping must carry its own `"default"`, so a symbol the fit never saw
  cannot silently inherit the class default; `trail >= stop` is now checked
  symbol by symbol rather than by extremes.
- `docs/research/R26-per-symbol-exit-width.md`.

### Findings

- **The per-symbol exit optimum is noise.** Fitted on m5_mine over a six-point
  grid and compared with the same fit on m5_validate: the argmax agrees for
  **1 of 12** symbols (chance is 2), mean curve correlation across windows is
  **-0.202**, and the rank correlation of the fitted optimum is **+0.000**.
- Out of sample the fit beats the single global width on m5_test (+3.22% vs
  +1.47%) and loses on m5_validate (+0.54% vs +1.64%). **Twelve fitted
  parameters do not beat one.**
- **The one-parameter wide exit is net positive on both later windows** at the
  full 3.00 bps cost: m5_validate **+1.64%** (net +0.79 bps/trade), m5_test
  **+1.47%** (+0.98). m5_mine remains -1.24%. First positive out-of-sample
  result in this project.
- Read against context, not in isolation: ~+2.2%/year, below T-bills and far
  below the +24% R20 measured for holding the indices; and R25 §4 still has the
  same parameters failing on 2 of 6 out-of-universe cells.

### Validation

- 376 unit tests pass (6 new: scalar behaviour unchanged, a mapping must name
  its default, a named width binds, an unnamed symbol takes the default,
  per-symbol trail/stop ordering, positivity).
- Three trials appended to `results/search/ledger.jsonl`, verdict REJECTED.
  m5_test now carries 8 trials, m5_validate 7, m5_mine 44.

## 2026-09-07e (the exits are too tight — the first mechanism that improves gross)

### Findings

- **The average LOSING trade is up +34 to +39 bps before it ends at -52**, in
  all three windows (MFE +34.1/+35.4/+38.6 against gross -52.2/-51.9/-56.1).
  Winners keep 0.61-0.63 of their best. `corr(hold_bars, gross)` is +0.46 to
  +0.59, so the problem is not holding too long.
- **Tightening the exit raises the hit rate and destroys the return.**
  `max_giveback` 0.3 lifts hit rate to 0.484/0.479/0.492 from 0.368/0.354/0.387
  while return falls to -14.19%/-3.89%/-5.25% from -7.95%/-4.23%/-1.36%, and the
  trade count RISES (3153 -> 3949) as capped trades re-open. R24 §1 shown
  causally: hit-rate-raising mechanisms cut the right tail, and the tail is the
  P&L.
- **Widening the exit improves every metric in every window** on ml12
  (`trail_sigmas` 3.5, `stop_sigmas` 2.0, `initial_stop_atr` removed): return
  -7.95%/-4.23%/-1.36% -> **-1.24%/+1.64%/+1.47%**; gross +1.11/+1.56/+2.38 ->
  **+2.55/+3.79/+3.98**; hit 0.368/0.354/0.387 -> 0.492/0.473/0.494; and the
  top-5 share of gross FALLS 86%/193%/85% -> 61%/121%/77%.
- **It does not fully replicate out of universe.** Hit rate rises in 9/9
  universe-window cells and the trade count roughly halves everywhere, but gross
  improves in only 3 of 6 out-of-universe cells, worsens in 2 (SPY+QQQ m5_test
  +0.74 -> -1.24; us_liquid_22 m5_validate +0.21 -> -1.53) and is flat in 1.
- Two hypotheses rejected first: sizing proportional to sigma (sign flips on
  m5_mine) and capping the give-back (above).
- The 2-ATR "Turtle-style" initial stop cap does most of the damage; removing it
  is most of the gain.

### Added

- `docs/research/R25-the-exits-are-too-tight.md`.

### Validation

- Three trials appended to `results/search/ledger.jsonl`, verdict PARTIAL.
  m5_test now carries 7 trials, m5_validate 6, m5_mine 43.
- 370 unit tests pass; no production code changed (every arm is a config
  override of existing parameters).

## 2026-09-07d (conviction-only entries: works mechanically, no edge)

### Findings

- **Screened all 19 setup features against gross return** (book unbound, both
  later windows, Bonferroni |t| = 3.01). Ten clear it in both windows with the
  same sign; the strongest are volatility and size (`horizon_sigma_bps` IC
  -0.220/-0.179). **`momentum_z` ranks worst of the nineteen** (t = +2.65/+0.04).
- **Rank IC and the mean disagree, and the mean is what compounds.** Mean gross
  by horizon-sigma quintile RISES (m5_validate -1.78 -> +15.78; m5_test +0.70 ->
  +5.28) while the rank IC is negative. High-sigma setups lose more often and
  earn more on average. **Selecting for hit rate selects against expected
  value.**
- **No conviction arm is positive in all three windows.** Seven arms swept. The
  tightest (z>=1.5, rvol>=2.0, acceptance_bars 3, max_positions 1) returns
  **-3.83% / +2.99% / +3.65%** on m5_mine / m5_validate / m5_test: best on the
  two windows it was selected on, negative on the largest window it was not.
- Session-clustered bootstrap on that arm: 95% CI on gross is [-8.6,+11.7],
  [-7.7,+20.8], [-6.6,+25.4] — all contain zero. **The top 5 trades carry
  143-379% of total gross in every window**; remove five trades and all three
  are negative.
- Hit rate does rise and does replicate (0.354 -> 0.391, 0.387 -> 0.458), which
  is precisely the wrong thing to optimise given the skew.
- **Cutting ~90% of trades removes ~90% of the loss.** That is a per-trade
  expectation which is a small negative constant — a toll, not a signal — and
  the third independent confirmation of R23 §5. `z1.5 rvol2 accept3` turns
  -7.95%/-4.23%/-1.36% into **-1.06%/-0.16%/+0.03%**: it stops the bleeding in
  every window without making money in any.

### Added

- `docs/research/R24-conviction-only-entries.md`.

### Validation

- Three trials appended to `results/search/ledger.jsonl`, all REJECTED.
  m5_test now carries 6 trials, m5_validate 5, m5_mine 42.
- 370 unit tests pass (no production code changed; every arm is a config
  override of existing parameters).

## 2026-09-07c (why the gate did not raise the win rate)

### Added

- `SRMomentumStrategy.veto_consumes_setup` — whether a break seen while vetoed
  is destroyed (like the opening clock gate) or merely postponed. Defaults to
  postponing, the behaviour R22's numbers were measured with.
- `docs/research/R23-why-the-gate-did-not-help.md`.

### Findings

- **The gate does remove bad trades.** Decomposing the two arms on m5_test by
  (symbol, entry time): 33 baseline trades vanished, averaging **-5.60 bps**;
  hit rate rose 0.3866 -> 0.3884.
- **But it is not a subtraction.** 18 trades appeared that the baseline never
  took, 11 of them the same setup entered later the same session, earning
  **-27.7 bps** against the trade they replaced. Making the veto consume the
  setup measured *worse* (gross +2.58 -> +2.37) and produced **more** new trades
  (18 -> 59): freeing the slot earlier lets the book admit substitutes.
- **With the book unbound (max_positions 12, full on 0.2% of bars) the gate's
  entire effect is +0.06 bps and the hit rate does not move.** Everything
  visible at max_positions 6 was substitution — R17 from a new direction.
- **The momentum premise does not hold.** Rank IC of momentum z against forward
  return, inside vs outside a range: m5_validate has outside *more* wrong-signed
  (-0.021 to -0.027 vs ~0); m5_test has the opposite sign at 12-24 bars
  (+0.013/+0.016 vs -0.007/-0.010). Sign flips between windows, all |IC| < 0.03.
- **Two candidate improvements tested and rejected**, each convincing on one
  split and gone on the other: mid-session-only entries (11:00-14:00: +5.32% on
  m5_validate, -3.13% on m5_test) and range edges as levels (+2.64 bps t=+3.38
  vs -0.16 bps t=-0.24).
- **Cost is what decides this strategy, not signal.** Total return by assumed
  round trip: m5_validate +2.88% / +1.43% / -0.02% / -1.40% / -4.23% and m5_test
  +4.34% / +3.26% / +2.09% / +0.99% / -1.36% at 0.50 / 1.00 / 1.50 / 2.00 / 3.00
  bps. **Positive in both windows at 1.00 bps, negative in both at 3.00.** The
  twelve names are mega-caps whose midday spread is a penny; the flat 3.00 bps
  is very likely far too pessimistic there.

### Validation

- 370 unit tests pass (1 new pinning that a consumed setup does not fire while a
  postponed one does).

## 2026-09-07b (market_state's persistence model as a gate: no tradable edge)

### Added

- `consolidation_gate.persistence_probability` / `persistence_veto` — read
  market_state's **walk-forward** `predictions_gbdt.parquet` (never the final
  joblib fit, which saw every row) and veto new entries while P(the box holds
  the next 6 closes) is above a threshold. NaN means "no box", which is not a
  veto. The uniform one-bar decision lag is asserted, not assumed.
- `config/universe/consolidation_ml_12.yaml` and
  `config/backtest/sr_momentum_ml12_5min.yaml` — the twelve names the model
  covers out of sample throughout; only the universe and run_id differ from
  `sr_momentum_5min.yaml`.
- `docs/research/R22-consolidation-persistence-gate.md`.

### Findings

- **No usable edge.** Best swept threshold (0.60) lifts gross from +0.58/+1.56/
  +2.38 to +0.83/+1.52/+2.58 bps on last_year/m5_validate/m5_test, against a
  **3.00 bps** round trip. Every arm in every window is net negative. The
  winning threshold is a selected maximum over five and is *worse* than baseline
  on m5_validate.
- Rank IC of P(hold) against per-trade gross is −0.093 / −0.054 / −0.108 with
  session-clustered permutation p = 0.252 / 0.287 / 0.331. Correct sign, three
  times, but only two of the windows are independent and neither is
  distinguishable from zero (Fisher p ~ 0.32).
- **Oracle bound.** Substituting the realised label for the prediction —
  look-ahead, untradable, an upper bound on any model of this target — is worth
  +1.09 to +1.18 bps of gross. On m5_test that reaches net **+0.47 bps**
  (+1.01% over eight months); on last_year it is still **−1.24 bps**. The
  shipped model captures ~20% of the oracle, which is what AUC 0.73 against a
  0.44 base rate should give: **the ceiling is the problem, not the model.**
  The oracle's whole margin is smaller than the known 5x error in the flat cost
  model (C00 §3b) over the session hours where entries concentrate.
- The rule detector is unchanged upstream: R21's binary gate reproduces exactly.

### Validation

- 369 unit tests pass (3 new pinning that an uncovered bar is not a veto, the
  threshold semantics, and refusal of predictions with a non-uniform lag).
- Two trials appended to `results/search/ledger.jsonl`, both REJECTED;
  m5_test now carries 5 and m5_validate 4.

## 2026-09-07 (a consolidation gate: no entry while price is inside a range)

### Added

- `SRMomentumStrategy.entry_veto` — an opaque `timestamp x symbol` boolean frame
  that refuses **new** positions. It vetoes openings only; an existing position
  is still managed and exited normally. The strategy learns nothing about what
  produced the frame, so any regime detector can be tested as a gate.
- `qtrader/experiments/consolidation_gate.py` — adapter calling
  `market_state.structure.consolidation` from the sibling repo. The detector is
  imported, not copied (ADR-0008); it always runs on the 5-minute series and is
  carried to a finer decision grid with `align_to_fine`.
- `scripts/run_consolidation_gate.py` — baseline / gated / complement over one
  shared panel, plus the baseline's own trades split by detector state.
- `docs/research/R21-consolidation-gate.md`, `docs/adr/ADR-0008-*`.

### Findings

- **The gate has no discriminating power.** The detector marks 42.3% of bars as
  in-range. Gating improves total return on both grids (5-min -4.77% -> -4.63%;
  1-min -1.59% -> -1.01%) but moves the **gross edge per trade** by at most
  0.08 bps against a 1.50 bps toll. Scaling the baseline loss by the surviving
  trade count predicts the gated result: -1.59% x 202/303 = -1.06% vs -1.01%
  measured. It is a way of trading less, not of trading better.
- **The sign of its apparent selection flips between grids.** Scoring the
  baseline's own trades by the state at their decision bar: 5-minute grid
  inside +2.57 bps vs outside -0.52 (Welch t = +0.77); 1-minute grid inside
  -0.52 vs outside +0.76 (t = -0.46). Session-clustered permutation tests give
  **p = 0.645** and **p = 0.763**.
- The most attractive number produced (5-minute `only entry in range`, -0.71%,
  gross +0.99 bps) is the same statistic as the p = 0.645 cell.
- Consistent with C00 §3a: a veto is not a subtraction. There is no candidate
  score with real IC for a filter to concentrate.

### Validation

- 366 unit tests pass, including three pinning that the veto blocks an entry,
  does not close an open position, and ignores symbols it does not mention; and
  five pinning the adapter's shape, truncation invariance and the one-bar delay
  when the mask is carried to a finer grid.

## 2026-09-06 (removed the 3-bar experiment)

### Removed

- `src/qtrader/experiments/three_bar.py`, `tests/unit/test_three_bar.py`,
  `docs/research/R21-three-bar-audit.md`, and sections 11-12 of
  `session_lab.ipynb`. The rule was audited to destruction the day before (see
  the 2026-09-05 entry); nothing else imported it. **These files were never
  committed, so this deletion is not recoverable from git** — the findings below
  are the only surviving record.
- A dangling `(R20/R21)` citation in the notebook's "reading results from a
  single day" caveat, corrected to `(R05, R20)`.

### Findings (carried over from the deleted R21, so they are not lost)

- The 3-bar rule's apparent win was an uncosted return (+0.186 bps/trade gross,
  **t = 1.18**, n = 11,411) measured against a baseline that discarded every
  overnight gap. Real QQQ buy and hold over the window was **+37.18%**, not the
  +11.51% reported; net of 1.50 bps the rule returned **-78.03%**.
- **Leverage does not offset a proportional cost.** Gross edge and slippage are
  both quoted on notional, so leverage scales them by the same factor and the
  break-even round-trip cost stays at the gross edge (0.186 bps) at any
  leverage. Measured: 2x -> -95.3%, 5x -> -99.96%, 10x and 20x -> ruin, worst
  20x day -52.8%. Only *fixed* per-ticket fees are amortised by size.
- **Variance drag alone kills high leverage here.** With cost set to zero, 20x
  still returns **-82.3%**: per-trade sigma is 16.8 bps against a 0.186 bps
  mean, so `L*mu - (L*sigma)^2/2` turns negative well below 20x (Kelly ~6.6x, on
  an edge that is not statistically distinguishable from zero).

### Validation

- 358 unit tests pass; `session_lab.ipynb` re-executed end to end.

## 2026-09-06

### Added

- Trade panel size buttons: 25% / 50% / 75% / 100% of free cash as margin.
- sim-trader draws an **Open** price line at the first fill and a dashed
  **Add N** line plus a bar marker for each subsequent lot. The Open label
  also shows live floating P&L (`Open +$12.34`).

### Changed

- sim-trader keeps cash, open position, and history when the date or
  symbol changes. Playback **Reset day** only rewinds the tape. A header
  **Reset** button restores $10,000 and clears the book.
- sim-trader keeps the current zoom while bars append during Play. The
  default left-pinned window is only reapplied on a new session or Reset.

### Fixed

- **sim-trader add/reduce sized remaining shares from the original entry.**
  Reduce used `qty * (margin / total margin)`, which is `notional / first
  (or average) entry`. Add/reduce now size shares at the slipped price of that
  action. Each add is a separate lot; reduce peels lots LIFO and realises P&L
  against those lot entries, not the first fill.

### Validation

- Typecheck `sim-trader` (`tsc --noEmit`).

## 2026-09-05 (the 3-bar rule's win was uncosted, and its baseline was not buy and hold)

### Fixed

- **`three_bar` charged no transaction cost while trading 27 round trips per
  session.** `run_three_bar` and `run_range` now take `cost_bps`, charged once
  per round trip. Trades carry `gross_bps` / `cost_bps` / `net_bps`, and
  `session_summary` reports `gross_return` and `return` (net) separately —
  previously gross figures were published under the names `net_pnl`/`net_bps`,
  which is how this went unnoticed.
- **`daily_returns` called intraday open→close "buy_hold".** It now reports
  `intraday_hold` (open→close, the risk-comparable baseline for an
  overnight-flat rule) *and* `buy_hold` (close-to-close, seeded from the first
  session's open), which compounds to `last close / first open − 1`.
- Collapsed a dead `elif`/`else` pair in the opposite-run counter (both branches
  reset it identically).

### Findings

- **The 3-bar rule does not beat buy and hold.** QQQ, 2025-01-05 → 2026-09-04,
  417 sessions, 11,411 round trips. Reported +21.70% vs "buy & hold" +11.51%;
  actual buy and hold over the same window is **+37.18%**, and net of 1.50 bps
  the rule returns **−78.03%**.
- Gross edge is **+0.186 bps per trade, t = 1.18** on n = 11,411 — not
  distinguishable from zero. 0.50 bps of cost takes it to −31.21%.
- **One basis point of stop slippage flips the sign** (+0.186 → −0.183 bps per
  trade) on its own, before any spread. 5,303 of 11,411 exits are stops filled
  at exactly the stop price.
- The take-profit signalled and filled at the same bar's close, violating the
  repo's execution-lag invariant. Not the source of the profit — lagging it
  correctly raises gross to +0.282 bps — but fixed for consistency.
- Overnight again accounts for the whole index return: intraday +13.61 points
  vs overnight +22.54 points. Same conclusion as R20, reached from a rule that
  shares no code with `sr_momentum`.

### Added

- `docs/research/R21-three-bar-audit.md`.
- Tests: `test_the_round_trip_cost_is_charged_once_per_trade`,
  `test_buy_and_hold_keeps_the_overnight_gap`.

### Validation

- 364 unit tests pass; `session_lab.ipynb` re-executed end to end.

## 2026-09-03 (the scale-out fired far too early; moved to the coarse trend)

### Added

- `sr_momentum.exhaustion_source` (`decision` | `coarse`) — which series the
  scale-out watches. Default unchanged.

### Findings

- **The scale-out was cutting size before trades worked.** Measured over 216
  scaled positions on the trailing year: the cut landed at a median of **bar 7
  of an 88-bar position** — 8% of the way in — at **+2.8 bps**, on positions
  whose eventual best was **+18.6 bps**. 74% of cuts happened inside the first
  ten bars.
- The cause is structural: the decision grid's own statistic **peaks at the
  entry by construction**, because the entry required it to be strong. Any decay
  test on it therefore fires within a few bars of opening. On a 1-minute grid it
  is measuring "the last minute was quiet", not "the move is over".
- Reading the 5-minute trend instead fixes the timing: the cut moves to a median
  of **bar 28 of 138** at decay 0.5, and **bar 40 of 152** at decay 0.35, with
  profit at the cut rising +2.8 -> +3.8 -> +5.0 bps and the number of cuts
  falling 216 -> 168 -> 152.
- It does not improve returns: -1.60% -> -1.76% -> -1.99%. Holding a full
  position longer on a zero-mean process is more exposure, not more edge.
- **No decay rule can catch the reversal itself.** At the low of a move momentum
  is at its *maximum*; a decay test necessarily fires after the move has faded,
  which is later and at a worse price. Catching the turn needs a reversal
  predictor, and R18 measured the reversal statistic at rank IC +0.0001.

### Validation

- 384 tests pass. New: the coarse source fires later than the decision grid, and
  an unknown source is refused.

## 2026-09-03 (peak-relative exhaustion, add-back, and the metric bug once more)

### Added

- `sr_momentum.exhaustion_decay` — scale out when momentum has fallen back from
  **its own peak during this trade**, replacing the absolute-floor version for
  the case it was built for. `exhaustion_z` (the floor) is kept and still off.
- `sr_momentum.allow_add_back` — restore a scaled-down position to full size
  when the entry rule would open that side again. The stop anchors are
  deliberately not reset, so topping up cannot loosen protection already earned.
- `sr_momentum.reverse_on_reversal` — stop and reverse rather than stand aside.
- `features/multiframe.align_to_fine` — a coarse series on a fine grid, visible
  only once each coarse bar has closed. `config/backtest/sr_momentum_index_1min.yaml`
  runs the indicators on 1-minute bars with the 5-minute series as trend context.
- `viz/setups` marks a **position cut back but not closed** with a hollow marker.

### Fixed

- **The absolute exhaustion floor fired at the start of moves, not the end.**
  Traced on QQQ 2025-11-20: it halved the position at 35 bps of profit with
  |z| = 1.04, immediately before a 339 bps continuation during which |z| rose to
  5.8. At the low of a strong move momentum is at its *maximum*; a floor cannot
  express "the move is over".
- **The gallery headline used the same unweighted mean that inflated
  `search.py`.** It read "+1.99 bps against a 1.50 bps cost" — profitable —
  where the capital-weighted figure is **+0.24 bps**. Scale-outs split a
  position into a half-size slice counted as a full observation.

### Findings

- 1-minute indicators with a 5-minute trend gate: **-1.59%** over the trailing
  year against -4.77% for the 5-minute config, but it loses less mainly by
  trading less — turnover 2.41 -> 1.03x/day, trades 711 -> 303. Gross +0.26 bps
  at t = +0.19 against 1.50 bps of cost.
- Scale-out and add-back are both neutral-to-slightly-negative:
  -1.59% -> -1.60% -> -1.71%. Max drawdown improves (-2.56% -> -1.87%) and
  Sharpe worsens.
- **Stop-and-reverse, thresholds fitted on one day, is the worst configuration
  tested in this project**: -19.23% on the trailing year against -4.77%
  baseline, turnover 9.17x/day, mean hold 31 -> 6.8 bars, 52% of trades lasting
  three bars or fewer. $15,357 of costs against -$3,873 of gross.

### Validation

- 382 tests pass. New: peak-relative exhaustion, add-back invariants (never
  exceeds full size, never flips side, off by default), the flip's weight cap
  and session-clock refusal, three for `align_to_fine`, three for the scale-out
  marker.

## 2026-09-03 (exhaustion scale-out; and a metric that inflated with it)

### Added

- `sr_momentum.exhaustion_z` / `exhaustion_keep` — when the momentum that
  justified a position decays below `exhaustion_z` **while the trade is ahead**,
  the position is reduced to `exhaustion_keep` of its size. Once per position,
  never while losing, so it is a profit rule rather than a stop wearing one's
  name. Off by default. Five tests.
- `SessionLab.triggers(run)` and two notebook sections: every gate's value at
  the decision bar for each trade, and the formulas the gates use, taken from
  the implementations.

### Fixed

- **`gross per trade` was unweighted, and any rule that scales a position
  inflated it.** A half-size scale-out slice counted as a full observation, and
  the slice is closed at a selected (profitable) moment. On the same run the
  exhaustion rule reported **+2.95 bps at t = +3.28 with a "net positive"
  verdict** while total return *fell*; weighted by capital at risk it is
  **+0.12 bps at t = +0.12**. `diagnose_shortfall` now weights by notional and
  uses the Kish effective sample size for the t-statistic, and
  `performance_summary` reports `gross_bps_weighted` / `net_bps_weighted`.

### Findings

- **Worked example, QQQ 2026-04-08.** Short at 09:45, held to the 15:50 flatten,
  +23.4 bps gross. Initial stop **37 bps** (2 x ATR, tighter than the 79 bps
  sigma stop), trail give-back **118 bps**. The trade's whole favourable
  excursion was **68 bps** and its worst adverse was **21 bps**, so neither
  barrier could ever fire: it sat in the dead zone between them all day and
  exited on the clock. The trail is calibrated to `sigma_H`, not to the
  excursion the trade actually produces.
- **No long was available during the midday rally** because the strategy was
  already short the same symbol: entry requires `position == FLAT`, and it held
  a position on 10 of 12 bars in that window, proposing 0 entries. A symbol in a
  position cannot take the other side; the only route out is an exit.
- The exhaustion rule is a small genuine improvement on the trailing year —
  total return **-4.77% -> -4.05%**, max drawdown **-7.22% -> -5.81%** — and
  still not an edge: gross +0.13 bps at t = +0.12 against 1.50 bps of cost.

### Validation

- 368 tests pass. Notebook re-executed end to end after the metric change.

## 2026-09-03 (single-session per-trade formula audit)

### Added

- `SessionLab.triggers()` now reconstructs every actual entry from its original
  strategy decision bar and reports decision OHLCV, the live S/R break and
  acceptance state, momentum numerator/scale, RVOL denominator, session VWAP,
  ATR/sigma stop geometry, next-open gap, holding period, and gross/cost/net
  outcomes. Thresholds are read from the actual run, including notebook
  overrides.
- `SessionLab.formula_audit()` expands every trade into eight symbolic formulas
  with that trade's numerical inputs substituted: break, side hold,
  acceptance, momentum, participation, VWAP, initial risk, and execution.
- `SessionLab.trigger_windows()` exposes the six preceding bars, the original
  decision bar, and the next-open fill bar for every trade; the fill is
  explicitly marked as unavailable to the signal.
- `notebooks/exploratory_only/session_lab.ipynb` now displays all trades both as
  a comparison table and one-trade-at-a-time audit, the formula substitutions,
  expanded bar state, and each trigger's OHLCV/indicator context window. It also
  documents the signed long/short ratchet and the close-decision/next-open-fill
  timing precisely.

### Validation

- The notebook executed end to end in the project `quant` environment: 15 code
  cells produced outputs with zero errors. The example session produced three
  trades and eight formula-audit rows for each trade.
- 361 tests pass, including seven trigger-detail tests for decision-time
  sampling, run-specific thresholds, break/stop reconstruction, complete
  formula expansion, and strict decision/fill separation.

## 2026-09-02 (one-day lab; and why the entry gates are not independent)

### Added

- `qtrader/experiments/session_lab.py` — `SessionLab`: load one session with its
  warm-up once, then re-run the strategy against the cached bars while
  parameters change. `run()`, `summary()`, `trades()`, `sweep()`.
- `notebooks/exploratory_only/session_lab.ipynb` — interactive single-day
  tuning: entries and exits on the standard `price_chart`, the day's return
  against holding the symbol over the same hours, per-parameter sweeps, and a
  bar-by-bar view of every entry gate. The notebook holds no strategy logic
  (CLAUDE.md §18); it calls the module. Executed end to end before shipping.

### Findings

- **Momentum does not exist on these indices to chase.** Within-session
  5-minute autocorrelation is ~0 at every lag (|rho| <= 0.033), and past-k
  against next-k rank IC is between -0.02 and +0.005 for k in 3..24, every
  |t| < 1.6.
- **So the whole loss is the toll.** Trailing year: gross **-0.06 bps/trade**
  (t = -0.04), cost 1.50, net -1.56 x 711 trades = -11.1% of notional, which at
  47.7% average exposure is the -4.77% observed. Zero gross, paid for 711 times.
- **The three entry gates are one gate counted three times.** `momentum_z` and
  `vwap_side` point the same way **85.2%** of bars; each correlates +0.84 and
  +0.62 with drift-since-the-open but only +0.23 and +0.34 with the last six
  bars. They measure where price sits relative to the session open, not where it
  is going. **30.6%** of firing bars fire against the last six bars' direction.
  Worked example: QQQ 2026-01-29, shorted at 11:30 after price had risen 49 bps
  over six bars, because cumulative drift was still -159 bps and VWAP side did
  not flip until 11:40.

### Fixed

- `plot_setups.py` hardcoded "against a 3.00 bps round-trip cost" in its lede
  while the index configs charge 1.50 — wrong about its own headline number. It
  now reads the cost from the config.
- The same page advertised a Kronos legend, title and explanation even when no
  forecast was requested; all three are now conditional.

### Validation

- 354 tests pass. New: eight for `SessionLab` (warm-up depth, session isolation,
  context reuse, refusals).

## 2026-09-02 (trailing year vs buy-and-hold: the return was all overnight)

### Added

- `scripts/compare_buy_and_hold.py` — strategy against holding the same
  instruments, with exposure adjustment and an intraday/overnight decomposition
  of the benchmark. An intraday strategy is flat by the close, so the overnight
  component is not a return it under-performed; it is one it never competed for.
- `last_year` split (2025-09-01 -> 2026-08-29), labelled in `splits.yaml` as a
  calendar window that straddles `m5_validate` and `m5_test` and must never be
  cited as out-of-sample evidence.
- `docs/research/R20-versus-buy-and-hold.md`.

### Findings

- Trailing year, SPY + QQQ at 1.50 bps per round trip: **strategy -4.77%**
  (Sharpe -0.79, maxDD -7.22%) against **+24.27% holding the two equally**
  (Sharpe +1.44, maxDD -11.25%). The strategy held a position on 72.3% of bars
  at 47.7% average gross exposure, so it is not a small bet that lost.
- **Essentially the entire index return was earned overnight.** Open-to-close
  across the year: SPY **+1.42%**, QQQ **-1.44%**. Close-to-open: **+18.98%**
  and **+29.50%**.
- This reframes the shortfall. The strategy did not lose 29 points by trading
  badly; it spent the year competing for a pool worth roughly zero while the
  benchmark collected a return available only to positions held through the
  close. Against an intraday-only benchmark the shortfall is real but far
  smaller.
- Worth recording for what to build next: if the tradable structure over this
  year was in the close-to-open gap rather than inside the session, this project
  has spent its entire effort on the wrong side of the clock. Testable in a few
  lines against data already stored.

## 2026-09-02 (SPY/QQQ: first positive numbers, and why they are not an edge)

### Added

- `config/universe/broad_index.yaml`, `config/backtest/sr_momentum_index_5min.yaml`
  — SPY and QQQ, 1.50 bps per round trip, book sized so it cannot bind.
- `docs/research/R19-broad-index.md`.
- Downloaded QQQ 5-minute and SPY/QQQ 1-minute bars (509,475 bars) for the
  spread measurement.

### Findings

- **First net-positive results in the project.** `m5_mine` +5.72% (gross +2.97
  bps, t = +2.02), `m5_validate` +6.75% (+4.23 bps, t = +1.56), `m5_test`
  -1.45% (+0.82 bps, t = +0.40). No parameter was fitted on index data —
  `momentum_z_min: 0.25` came from R16 on stocks — so even `m5_mine` is
  out-of-sample for the parameter choice.
- **The cost assumption is conservative here, the reverse of R10.** Roll
  half-spreads on 1-minute bars: SPY **0.37 bps** round trip, QQQ **1.19**,
  against 1.50 charged. Breakeven is 2.97 / 4.23 / 0.82 bps by window, so the
  first two clear the measured spread with room.
- **But the result is five days.** Top 5 sessions are **141%** of the total on
  `m5_mine` and **188%** on `m5_validate`; removing them turns every window
  negative. Median day is -$37 / +$6 / -$58 and up days are 43-51%.
- **The leg rotates**: QQQ carries `m5_mine` (+5.56 bps) while SPY is flat
  (+0.51); SPY carries `m5_validate` (+5.35) while QQQ fades (+3.16); QQQ turns
  negative on `m5_test` (-1.19). Same signature as R12's short-leg inversion.
- **The t-statistics are overstated by ~1.25x**: daily P&L correlation between
  the legs is +0.48 to +0.68, so two symbols are 1.2-1.35 independent legs, not
  2. `m5_mine`'s +2.02 is nearer +1.6.
- Verdict: not distinguishable from noise with a fat right tail, which is what a
  breakout strategy on a trending index looks like with or without an edge.

### Corrected

- I first assumed `round_step: 1.0` would be far too fine on a $554 index. What
  matters for a level is spacing against the bar range, not bps: **$1 is 2.38
  ATR on SPY and 2.01 on QQQ** against 1.2-3.1 for the single stocks. The config
  was run unchanged.

## 2026-09-02 (displacement: built, measured, and the ranking statistic is a coin)

### Added

- `sr_momentum.displace_margin` and `_resolve_book`, replacing
  `_respect_book_limit`. A queued candidate may evict a holding only when that
  holding is **currently losing**, is the weakest replaceable one, and the
  candidate beats it by at least the margin. The book never grows; winners are
  never displaced. Off by default.
- `docs/research/R18-displacement.md`.

### Findings

- **The book's ranking statistic carries no information.** `|momentum_z|` at
  entry against realised gross return over 4,236 trades: **rank IC +0.0001,
  t = +0.00**. The strongest-conviction quintile returns +0.34 bps against
  +4.33 for the weakest, and the hit rate *falls* as conviction rises.
- **Displacement is worse or equal in seven of eight cells** and raises trade
  count and turnover in all eight — unavoidable, since each displacement adds an
  exit and an entry. It hurts rather than merely costing turnover because it
  makes the book act on an uninformative ranking more often.
- **A higher confidence bar is actively harmful**: gross/trade +1.80 bps at
  z=0.25, +0.64 at 1.0, **-1.28** at 1.5, -1.11 at 2.0. Trade count does fall as
  requested (4,236 -> 2,607) but each remaining trade is worse.
- **This settles R17.** The queue was not the binding problem — it was masking
  the fact that the strategy cannot tell its good candidates from its bad ones.
  Fixing the mechanism does not help when the ranking it enables is a coin.

### Validation

- 346 tests pass. New: eight for displacement, including a property test over
  200 random book states asserting the cap is never breached and nothing opens
  on top of a holding. It caught a real gap in the first implementation.

## 2026-09-02 (final holdout; and the book limit, not the signal, picks the trades)

### Findings

- **`m5_test`, the last untouched window**: the R16 configuration
  (`momentum_z_min: 0.25`, no fine filter) returns **-4.51%**, gross +1.52 bps
  at **t = +0.78** against a 3.00 bps round trip. Gross retained 84% of its
  in-sample value, but is indistinguishable from zero. All three 5-minute
  windows are now spent and none produced a tradable edge.
- **The book limit decides which trades happen, and it does not select** (R17).
  On the pictured NVDA session the strategy proposed entries six times and the
  book was full at all six; the one that traded had the *weakest* momentum of
  the six. Across `m5_test` the book is at its cap on **82.9%** of intraday
  bars, only **19.9%** of proposals become trades, and **92.4%** are made while
  it is full. On `m5_mine` the trades taken score **-0.46 bps** against **+0.71**
  for those rejected (t = -1.02) — admission is decided by exit timing, not
  signal strength.
- **This confounds every veto-filter experiment in the project** (R09, R10 §3,
  R11 §2). Each found that a filter raised the trade count; the mechanism is now
  quantified — a veto returns a slot to a queue saturated 83% of the time, which
  refills immediately with a candidate no better and no worse.
- LLM judgement of the 20 holdout gallery extremes: **13 keep, 5 veto, 2
  abstain**. Losers vetoed 3/10, winners falsely vetoed 2/10 — a 10-point
  separation on 18 verdicts, about one trade away from chance. 13 of 20 setups
  were read as `volatile`, and every veto came from one.

### Fixed

- **`preflight()`**: a model that is not served now stops a run, naming what is
  available. Previously a stale tag made all 20 calls 404, `on_abstain="keep"`
  turned each into a keep, and the summary reported "0 vetoes, 50% balanced
  accuracy" — indistinguishable from a model that judged everything and objected
  to nothing.
- **`DEFAULT_TIMEOUT` 45s -> 300s.** At 45s, 13 of 20 candidates timed out
  against the 27B model and were reported as abstentions.
- The gallery summary refuses to print rates when nothing was judged, and warns
  when only some were.
- Two candidates still abstain on `JSONDecodeError` — the model exceeds
  `num_predict` mid-`rationale` and the JSON never closes. Recorded, not yet
  fixed.

### Added

- `ValidationConfig.image_dir` keeps the chart each candidate was judged on; the
  journal row carries `image_path` and the full `prompt`, so a decision replays
  exactly without the model.
- `docs/research/R17-book-capacity.md`.

### Validation

- 338 tests pass. New: image persistence, and preflight on a missing model.
- Execution audit passes all five checks on `m5_test`.

## 2026-09-02 (multi-timeframe: threshold helps, 1-minute confirmation is null)

### Added

- `features/multiframe.py` — causal alignment of a fine grid onto a coarse one.
  A coarse bar labelled T closes at T+step, so it takes the LAST fine bar inside
  its own span; taking the bar labelled T+step would leak the first minute of
  the fill bar. Nine tests, including one that perturbs every fine bar from a
  cut point forward and asserts no earlier coarse value moves.
- `MarketContext.fine_panel` and `DataConfig.fine_timeframe`, both optional and
  defaulting to nothing, so no existing strategy or config is affected.
- `sr_momentum` gains `fine_momentum_z_min`, `fine_span`, `fine_vol_window`.
  A bar where the fine grid has no reading is a refusal, not a pass.
- `config/backtest/sr_momentum_5min_mtf.yaml` — the experiment, kept separate so
  the settled baseline is untouched.
- `docs/research/R16-multiframe-confirmation.md`.
- Downloaded 4,632,341 1-minute bars across 31 symbols: the existing 1-minute
  coverage began 2026-02 and did not overlap `m5_mine` at all.

### Findings

- **Lowering the coarse momentum threshold is a real improvement.** Gross per
  trade +0.64 bps (z=1.0) -> +1.80 (z=0.25); total return -12.80% -> -7.38%.
  Explained by mechanism: `session_drift_zscore` measures drift since the open,
  so on the traced TSLA short (2025-03-17) it read -0.40 at the 10:25 break
  while the previous six bars were -101 bps, and only cleared -1.00 at 11:40 —
  five bars before the session low.
- **The 1-minute confirmation carries no information.** Scored against each
  trade's own realised forward return on a sample not selected by the filter:
  **rank IC +0.0016, t = +0.22, n = 20,711**, and the agreement quintiles are
  flat (49.3%-50.0% hit rate throughout).
- The threshold grid's apparent variation is noise: the fine dimension is
  non-monotonic at every coarse level, and the best cell (coarse 0.50 / fine
  1.0, +1.83 bps) is statistically identical to coarse 0.25 with **no** fine
  filter (+1.80).
- Nothing in the grid is tradable. Best gross +1.83 bps against a configured
  3.00 bps round trip, best total return -6.12%.

### Validation

- 334 tests pass.
- Execution audit passes all five checks on the multi-timeframe path.
- Nine grid cells recorded; `m5_mine` has now carried roughly 40 trials.

## 2026-09-02 (LLM audit of gallery extremes)

### Added

- `scripts/test_llm_gallery.py` maps the exact best/worst setup-gallery episodes
  back to their original candidate runs, asks the existing veto-only LLM layer
  for a causal verdict, journals every call, computes discrimination metrics,
  and renders the 40 panels with the LLM action in each title.
- Setup galleries accept optional per-episode LLM decisions.
- `docs/research/R15-llm-gallery-veto-audit.md`.

### Fixed

- The LLM prompt now states the exact JSON-only output contract. The installed
  MLX Qwen renderer ignored Ollama's schema argument by itself and returned
  Markdown prose, which correctly became abstentions but made the layer inert.

### Findings

- On the exact 20 best and 20 worst R14 charts, Qwen3.6 vetoed **1/20 losers
  (5%)** and **0/20 winners**; 5 responses abstained under fail-open handling.
- Balanced accuracy was 52.5%. Among parseable decisions, confidence in the
  proposed side was higher for losers (0.815) than winners (0.783). The model
  mostly repeats the quant setup thesis rather than identifying failed
  continuation.
- The one correct veto removed 171 bps, only 4.2% of the selected worst group's
  summed gross loss. This screen does not justify a full LLM-filter backtest.

## 2026-09-02 (ATR initial stop and break acceptance)

### Added

- `sr_momentum.acceptance_bars`: requires consecutive post-break closes to
  continue in the breakout direction; the event bar cannot confirm itself.
- `sr_momentum.initial_stop_atr`: caps the initial sigma stop in ATR units while
  leaving the wider horizon-sigma trend ratchet unchanged.
- Decision diagnostics `acceptance_count` and `initial_stop_bps`, focused
  regression tests, and `docs/research/R14-atr-stop-and-break-acceptance.md`.
- Combined-strategy gallery with 20 best and 20 worst trades at
  `results/sr_momentum_5min__m5_mine/setups_atr_acceptance.html`.

### Fixed

- A traded breakout is consumed until price invalidates its boundary. A tight
  stop can no longer re-enter the identical, never-reset breakout repeatedly.

### Findings

- The pictured AAPL short improves locally: 2 ATR cuts it from −355 to −149
  bps; one-bar acceptance rejects it entirely. A MACD death-cross filter would
  not have helped because MACD was already bearish at the decision.
- The portfolio result is negative. Baseline: 2,809 trades, +4.02 bps/trade,
  +3.45% return. Combined: 4,037 trades, +0.64 bps/trade, −12.80% return.
  The combined 1% loss tail improves from −284 to −140 bps, but turnover rises
  from 2.54x to 3.66x/day and large winners are removed too.
- Keep the mechanisms as explicit risk/confirmation controls, but do not call
  this configuration an alpha improvement or deploy it on this evidence.

### Validation

- 45 focused and 324 full-suite tests pass.
- All five execution audit checks pass over 8,074 fills.
- `m5_test` remains untouched.

## 2026-09-01 (first ten minutes are observation-only)

### Changed

- The canonical `sr_momentum_5min` config now sets
  `no_entry_before: "09:35"`; because timestamps are bar opens and fills occur
  at the next open, this makes 09:40 the earliest possible fill.
- `no_entry_before` now consumes setups formed while the gate is closed. A
  pre-cutoff break cannot be entered on the first admitted bar; the level must
  invalidate and re-break, or a different level must break after the cutoff.
- Break and discarded-break state now reset at session boundaries.
- The engine cancels delayed targets when their lag would cross a session
  boundary. Pending intraday targets are day orders, including after an early
  close; they cannot execute at the next session's open.

### Added

- Focused regressions for cancellation of a blocked setup and admission after a
  genuine reset/re-break.
- `docs/research/R13-open-ten-minute-blackout.md`.
- A standard 20-best/20-worst gallery at
  `results/sr_momentum_5min__m5_mine/setups_open10_blackout.html`.

### Findings

- The constraint is binding: zero entries before 09:40 ET; earliest fill 09:40.
- It does not improve the strategy. On `m5_mine`: 2,790 trades, +4.33 bps gross
  per trade (t=+1.86), +4.69% total return, Sharpe +0.42, max drawdown −10.49%,
  and 2.52x/day turnover. The earlier reference was +4.33 bps gross and +4.86%
  total return: economically unchanged and still insignificant after mining.
- 53.5% of trades enter at 09:40. The gate is retained as an explicit
  constraint, not as alpha.

### Validation

- 49 focused tests pass; 316 full-suite tests pass.
- The execution audit passes all five checks over 5,558 fills.
- `m5_test` remains untouched.

## 2026-08-30 (out-of-sample test: the configuration did not replicate)

### Findings

- **`sr_momentum` was taken once to `m5_validate` and failed.** Gross per trade
  +4.33 -> **+1.36 bps**, t +1.86 -> **+0.50**, total return +4.86% ->
  **-5.24%**, Sharpe +0.44 -> -0.51, max drawdown -8.60% -> -18.44%. Gross
  retained 31% of its in-sample value and is indistinguishable from zero.
- **The short leg inverted**: +4.91 bps in sample against -3.10 out of sample, a
  swing of -8.0 bps per trade. It carried the in-sample result and is the larger
  loser out of sample. A structural effect does not change sign.
- **The session profile survived** — 50% of entries still decided in the first
  ten minutes, still the largest positive bucket — so the failure is a smaller
  edge rather than different behaviour, and R10 section 5's cost finding applies
  to this window unchanged.
- This is what ~33 trials at a best in-sample t of +1.94, against a Bonferroni
  bar near 3.3, predicts. The result is confirmation, at the cost of the window.

### Added

- `docs/research/R12-out-of-sample.md`.
- `sr_momentum` gains `max_giveback`, `exit_on_reversal`, `reversal_bars` and
  `exit_on_opposite_signal` — exits driven by the trend judgement rather than a
  fixed barrier. All off by default; all measured worse than the ratchet
  (R10 sections 5i and 5j). Across eight configurations the more responsive the
  exit, the better one traced trade looks and the worse the sample performs,
  monotonically from +4.86% to -35.65%.

### Removed

- Nothing. `sr_momentum` is retained as a measured negative result with its
  research notes, not carried forward as a candidate.

### Validation

- 312 tests pass.
- Execution audit passes all five checks on `m5_validate` over 4,150 fills.
- `m5_test` remains untouched. Both 5-minute research windows are now spent.

## 2026-08-30 (Kronos agreement required; previous-day bars removed)

Two instructions, both implemented in full and made the config default. This
entry records what they cost.

### Added

- `docs/HOWTO-galleries.md` — the commands to run the win/loss galleries and the
  related diagnostics, per strategy.
- `viz.setups.setup_gallery` is now strategy-agnostic: the level lines and zone
  are drawn only when the strategy publishes them, and the page title and legend
  adapt. Galleries rendered for all four registered strategies.
- `docs/research/R11-kronos-required-no-prior-day.md`.
- `average_true_range` takes `restart`, so the previous close is not carried
  across a session boundary.
- `config/backtest/sr_momentum_5min.yaml` now sets `use_previous_day: false`,
  `require_retest: false`, `sizing: equal`, `confirmation_path` and
  `confirmation_min: 0.0`, each with the measurement that justifies it.

### Fixed

- **ATR leaked the overnight gap into the break-zone width.** Today's first bar
  had a true range of `|high - yesterday's close|`, and ATR sets `level_zone`,
  which decides what counts as a break. Yesterday's price was setting today's
  break tolerance. Now session-local; pinned by a test.

### Findings

- **Removing previous-day levels is a small improvement**: total return
  +4.56% -> **+4.86%**, gross +4.27 -> +4.33 bps, hit rate 45.5% -> 45.7%.
  Consistent with R07, which had already measured that level family as null.
- **Requiring Kronos agreement costs about 12 points of total return**:
  +4.86% -> **-7.49%**, hit rate 45.7% -> 43.0%, maxDD -8.60% -> -14.15%. At a
  0.25 sigma threshold, -9.34%. Verified binding: all 40 charted panels carry an
  agreement mark.
- **The raw conditional is the strongest sign of life Kronos has shown**, and is
  still not significant: among trades taken without the filter, agreement is
  worth +6.84 bps against +2.72 for disagreement, **+4.12 bps at t = +0.83**
  (against +0.28 bps at t = +0.06 in R09).
- **Book capacity explains the gap, and this is the third appearance of the
  effect** (blackout in R10 §3, filter in R09, filter here). `max_positions=6`
  binds, so a veto does not remove an opportunity — it frees a slot that
  `_respect_book_limit` fills with a weaker candidate. Trades *rise* 2,761 ->
  2,923; turnover 2.50 -> 2.64x/day.
- Widening the book so a veto removes rather than reshuffles (12 slots at 0.075,
  same gross exposure) does not rescue the filter (-4.82% control -> -8.41%
  filtered) and is itself very costly: **the six-slot cap was doing real
  selection**, ranking candidates by `|momentum_z|` and keeping the strongest.

### Validation

- 248 tests pass. New: 1 pinning the ATR session boundary, 1 for a strategy
  with no level columns still rendering.
- Trials 29-33 recorded in the ledger; `m5_mine` now carries 33.

## 2026-08-29 (where the trades are, and what they should have cost)

Prompted by an observation on the setup charts that most decisions looked like
they were made on the opening gap. The observation was right; the diagnosis and
the proposed remedy were not, and chasing it down found a larger problem.

### Added

- `sr_momentum` gains `no_entry_before`, a clock-based session-open blackout
  symmetric with `no_entry_after`. Implemented, tested, and left **unset** — see
  Findings.
- `sr_momentum` gains `sizing` (`risk` | `equal`).
- `sr_momentum` gains `require_retest`, and publishes `watched_age` so the
  break state machine can be inspected from the episode bars.
- `sr_momentum` gains `momentum_estimator` (`session` | `ewma`) and
  `momentum_span`, matching the convention `trend_ratchet` already uses. Default
  unchanged.
- `viz.setups` panels now mark the **peak** — the best the trade ever looked
  between entry and exit — so the gap between it and the exit is visible.
- `docs/research/R10-open-concentration-and-costs.md`.

### Findings

- **49% of all entries are decided on bar 1 (09:35-09:40)**, 68% in the first
  thirty minutes.
- **The cause is round numbers, not previous-day gaps.** 76% of entries come
  from the round-number family against 14% from previous-day levels. $1 round
  levels are never far from the price, and the opening bars carry 41 bps of
  realised 5-minute volatility against 12 bps at 15:20, so levels get broken and
  retested constantly. (Attribution must be matched across the retest window:
  round levels are recomputed every bar, so matching only at the entry bar
  mislabels 42% of trades.)
- **Delaying entries makes it worse**: gross/trade +3.50 -> +0.40 (09:40) ->
  -0.27 (10:20), total return -3.02% -> -6.37% -> -11.28%. Turnover *rises*,
  1.80x -> 2.28x/day, because a blackout delays a live setup rather than
  cancelling it — the break re-registers every bar and the entry lands on the
  first admitted bar.
- **Dropping previous-day levels gives the first positive total return in the
  project** (+0.30%, gross +4.62 bps at t = +1.96, Sharpe +0.07). It should not
  be believed: it is trial 18 against `m5_mine`, where the Bonferroni threshold
  is |t| ~ 3.0, and half its trades sit in the bucket the cost model
  undercharges.
- **The cost model is flat and the spread is not.** Roll (1984) half-spreads on
  1-minute bars: 4.91 bps at 09:31-09:35 against 0.34 bps at 11:30-14:30 —
  **14x**. The configs charge 1.0 bps everywhere, which is ~3x conservative
  mid-day and ~5x optimistic in the first five minutes. Half of this strategy's
  trades are in the second group.
- **Under measured spreads both variants are clearly negative**: baseline
  +0.50 -> **-3.09 bps/trade**, no-previous-day +1.62 -> **-2.06 bps/trade**.
  Breakeven half-spread for the bar 0-1 bucket is 3.66-3.96 bps against 4.91
  measured.
- **This inflates every earlier number in the project** in proportion to how
  much of its turnover sits in the first half hour.

- **Why it performs so badly, decomposed.** The exit is not the fault: the
  ratchet beats every fixed horizon tested (+3.50 bps against a best fixed of
  +1.78 at 48 bars), and winners capture 64% of their maximum favourable
  excursion. The fault is entry direction — **66% of losing trades never saw
  their favourable excursion reach even half their adverse one**, mean MFE +38
  against MAE -98. They were wrong from the first bar.
- **The whole edge is 2 percentage points of win rate before costs**: 45.3%
  against a 43.3% breakeven, on a standard error of 0.94 points. About 2 sigma,
  matching the t = +1.51 on gross by an independent route.
- **The delay destroys it by selection, not by timing.** The payoff ratio
  improves (1.31 -> 1.44) while the win rate collapses through its own
  breakeven (45.3% -> 40.7% against 40.9%). A break-and-retest still alive at
  bar 10+ is one where price *failed to run* — waiting keeps the failures and
  discards the setups that resolved immediately.

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

### Validation

- 246 tests pass. New: 3 for the sizing rules; 2 pinning the break state machine (register once, then
  age; expire after `retest_bars`); 4 for the blackout, including one asserting that it
  *delays rather than cancels*, because that changes how its backtest reads;
  2 for the peak marker; 2 for the momentum estimator, including one that pins
  the `session` estimator's inability to reverse after a large early move.
- Trials 16-21 recorded in `results/search/ledger.jsonl`.

### Not done, deliberately

- **A time-of-day cost model was not built.** It is now the highest-value change
  in the repository, but it touches `backtest/costs.py`, needs regression tests,
  and would re-score every recorded result — too large to slip in as a side
  effect of an entry-timing question. Recorded as the next action.

## 2026-08-29 (S/R + momentum + Kronos: implemented, measured, falsified)

The conjunction rejected on paper in R08 was implemented in full at the project
owner's direction, so that the argument would be settled by measurement rather
than by assertion. It was, and the measurement agrees with the paper argument.

### Added

- `src/qtrader/features/levels.py` — ex-ante support/resistance. Previous-day
  high/low, opening-range high/low (NaN until the range has closed), round
  numbers, Wilder ATR, and the `max(1 tick, level_atr x ATR)` tolerance zone.
  Every level is knowable at the bar it is attached to; the alternative is
  hindsight charting.
- `src/qtrader/strategies/sr_momentum.py` — break -> retest -> hold, gated on
  momentum sign, relative volume and VWAP side, released by the same monotone
  volatility ratchet `trend_ratchet` uses. Publishes a `candidate` column so a
  heavy external model can be scored once on proposed entries and cached.
- `src/qtrader/models/kronos_confirm.py` + `scripts/kronos_confirm.py` — batched
  Kronos scoring of candidates. The context window ends **at the candidate bar
  inclusive**; the forecast begins after it. The predictor is injected, so the
  module carries no torch dependency and stays testable without one.
- `scripts/analyze_confirmation.py` — asks whether a confirmation score has any
  rank IC against the realised outcome of the candidates, before any backtest is
  written. Seconds instead of a backtest, and decisive.
- `config/backtest/sr_momentum_5min.yaml` — the report's stated starting values,
  not search results.
- `src/qtrader/viz/setups.py` — `setup_gallery`, the real-price counterpart to
  `episode_gallery`. Panels keep actual prices (a support level does not survive
  being rescaled per panel) and carry the level the rule watched with its
  tolerance zone, the entry marker pointing the predicted direction, and the
  Kronos forecast close path drawn forward from the decision bar.
- `scripts/plot_setups.py` — 20 best and 20 worst round trips as two galleries,
  rendered by identical code onto identical axes so only the data differs.
- `sr_momentum` now publishes `watched_level`, `watched_side`, `zone` and each
  level family as indicator columns. `analysis.episodes._window` already copies
  every indicator into the episode bars, so the charts get them with no new
  plumbing — and the level drawn is the one recorded inside the bar loop at the
  moment of the decision, never redrawn afterwards from the finished chart.
- `models.kronos_confirm.forecast_paths` — the forecast paths themselves rather
  than the scalar score, sharing one window builder with `score_candidates` so
  the two cannot drift apart on causality.
- `docs/research/R09-sr-momentum-kronos.md`.

### Fixed

- **The retest was never filtering anything.** `_track_break` tested "price is
  beyond a level" rather than "price was not already beyond it", so a break
  re-registered every bar, resetting `broke_age` to 0 and clearing `retested`;
  the same call then re-set it if that bar's range straddled the zone. 81.4% of
  live breaks sat at age 0. "Break -> come back -> retest -> hold" was in
  practice a single-bar test of "price is beyond the level and this bar
  straddles it". A break now registers once and ages; age-0 share falls to
  42.3%. Two regression tests pin it. Found because removing the retest
  requirement altogether changed the trade count by 19 out of 2,788.

- **`nearest_round_levels` bracketed the current close**, making
  `price > resistance` unsatisfiable and the entire round-number level family
  inert. The reference is now the previous bar's brackets, which is also the
  only causal version. Candidates 5,762 -> 15,646; the first baseline (+2.72 bps
  gross) is void and was superseded. Caught by writing the unit tests, not by
  reading the output — a silently dead component does not announce itself.

### Findings

- **Baseline `sr_momentum` on `m5_mine`, 2,788 trades: gross +3.50 bps at
  t = +1.51**, cost 3.00 bps, hit rate 44.1%, mean hold 48 bars. Total return
  −3.02%, Sharpe −0.47, maxDD −8.95%. Indistinguishable from zero, as R07
  (levels) and R04/R05 (intraday momentum) both predicted. Per-trade expectancy
  is +0.50 bps while the capital-weighted return is negative: the losers are in
  the larger positions, which is a sizing artefact and not an edge.
- **Kronos has no directional information about these entries.** Over 15,645
  scored candidates it agrees with the realised direction **49.3%** of the time
  at a 12-bar horizon (rank IC −0.0108, t = −1.35) and 49.1% at 48 bars
  (IC −0.0173, t = −2.16). The ">50% win rate" claim does not transfer to
  5-minute US large caps against a 3 bps round trip.
- **The filter makes the strategy monotonically worse.** Hit rate 44.1% ->
  41.4% -> 40.6% -> 40.4% as the threshold tightens through 0 / 0.25 / 0.50
  sigma; gross per trade falls from +3.50 to ~+1.2 bps and stays there. The
  short leg, the only positive sleeve at +4.95 bps gross, is turned negative.
  The filter removes winners slightly faster than losers.
- R08's arithmetic is confirmed on both halves: `A` uninformative (t = +1.51),
  `B` uninformative (IC ~ 0), so the conjunction is uninformative on a smaller
  sample.
- **The charts show a real setup with no conditioning power.** Every one of the
  40 charted panels has a genuine level being broken and retested, and winners
  are indistinguishable from losers in the bars before entry. Kronos agreed with
  the direction on 8 of 20 winners and 6 of 20 losers — noise on hand-picked
  extremes, pointing the same way as the rank IC.
- **Horizon mismatch worth naming**: Kronos was asked for 12 bars while the mean
  hold is 48. Measuring the score against a 48-bar outcome does not rescue it
  (IC -0.017), but the filter was never looking at the horizon it authorised.

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

### Validation

- Execution audit on `m5_mine`: all five checks pass over 5,540 fills — open of
  the next bar, prices inside `[low, high]`, spread always adverse, silent-bar
  fills all declared stale exits, and 2,839 earlier fills unchanged when every
  bar after 2024-08-13 was tripled.
- 233 unit + integration tests pass. New: 9 for the setup gallery (including
  both decision-bar lookups, which would silently draw nothing if read at the
  fill), 8 for the level features
  (availability, not just formulas), 11 for the strategy (including
  prefix-invariance against lookahead, and one condition removed at a time), 10
  for the Kronos harness against a stub predictor (including the window
  boundary).

### Not done, deliberately

- **`m5_validate` was not touched.** Nothing is positive on the mining window,
  so spending the confirmation window would burn a scarce resource to confirm an
  already-negative in-sample result.
- **The filter was not inverted**, though its negative IC means that would
  improve the backtest. Choosing a sign by looking at the window that produced
  it is data mining, at t = −1.35.
- **No parameters were searched.** One clean test, not the best of many.
- `m5_mine` has now carried 15 recorded trials; that burden attaches to anything
  found on it later.

## 2026-08-26 (S/R + Kronos conjunction: rejected on paper)

### Findings

- `docs/research/R08-kronos-conjunction.md` — proposal to confirm S/R entries
  with the Kronos foundation model, rejected without implementation.
  - **The conjunction fails on arithmetic before the model is reached.** R07
    measured the S/R leg as independent of forward returns (0.0006 sigma vs
    placebo, no |t| above 0.78), and for an `A` independent of `r`,
    `E[r | A and B] = E[r | B]`. Filtering with a null signal leaves expectancy
    per trade unchanged, shrinks the sample, and creates a selection that can
    look better or worse by chance — the standard route into overfitting.
  - Kronos alone fails gate §1 (no mechanism), §2 (public weights, public
    architecture, free OHLCV inputs), §3 (unchanged — still a slow taker) and §4
    (no stated edge magnitude; it outputs a price path, so converting it to a
    position adds free parameters).
  - The model's own README states it is "not a production-ready quantitative
    trading system" and makes no profitability claims.
  - §5 recorded with an honest qualification: Kronos is a different *function
    class* from the hand-crafted linear signals tested so far, so the rule is
    not a clean kill on its own. R05's shuffle test (real ordering produces
    trends at 0.543x the shuffled rate) bounds how much nonlinear structure is
    available, and any find must beat 2.4 bps per round trip when the strongest
    measured effect in this universe does not.
  - **Win-rate arithmetic added.** At a 12-bar hold the breakeven win rate here
    is **53.4%** (56.4% at 3 bars, 52.5% at 24), so 51% loses money at every
    horizon. The best signal found anywhere in this project — residual reversal,
    rank IC +0.017 at t = +9.4 — implies a **52.4%** win rate and is *below*
    breakeven. "Greater than 50%" claims something weaker than an already
    rejected signal.
  - **Filtering logic addressed.** "S/R false signals" presupposes true ones; a
    null leg has no correct subset to recover. If `A` is independent of `r`,
    `{A=1}` is a random subset and the conjunction is weakly worse than `B`
    alone in both branches — fewer opportunities at the same rate if `B` works,
    and no rescue if it does not.
- Recorded in `results/search/ledger.jsonl` as rejected-before-implementation.

## 2026-08-26 (S/R + order-flow report: assessed and rejected)

### Findings

- `docs/research/R07-sr-orderflow-report-assessment.md` — gate assessment of
  `deep-research-report.md`, plus two pre-registered falsification tests.
  - The report's strongest components (OFI, queue imbalance, absorption) are
    **limit-order-book** results and are unconstructible here: IEX gives 2.1% of
    AAPL's tape, no quotes, no book, and no way to sign a trade. The report says
    as much itself.
  - The implementable subset (ex-ante S/R, RVOL, VWAP state) fails the gate on
    **§3 which side is paid** — stop cascades pay whoever provides liquidity into
    them, not a taker arriving at the next bar's open — and on **§5 incremental
    information**, being functions of past prices already rejected.
  - **Round-number effect: absent.** Continuation after crossing $X.00 is
    −0.0035 sigma against −0.0030 for placebo offsets $X.13/$X.37; difference
    −0.0006 sigma, no |t| above 0.78 over 170,038 crossings. The report
    explicitly asked for this to be re-estimated for equities rather than
    transplanted from FX; it has been, and it is not there.
  - **Previous-day high/low: absent.** PDH +0.0100, PDL +0.0188 sigma against
    placebos at 37%/63% of the prior day's range spanning −0.0145 to +0.0128.
    The placebo range fully brackets both, no |t| above 0.63.
  - Since every rule in the state machine begins with "price approaches L", a
    null at the trigger makes the rest untestable rather than merely
    unpromising. **Verdict: do not implement.**
- Both rejections recorded in `results/search/ledger.jsonl`.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **The retest was never filtering anything.** `_track_break` tested "price is
  beyond a level" rather than "price was not already beyond it", so a break
  re-registered every bar, resetting `broke_age` to 0 and clearing `retested`;
  the same call then re-set it if that bar's range straddled the zone. 81.4% of
  live breaks sat at age 0. "Break -> come back -> retest -> hold" was in
  practice a single-bar test of "price is beyond the level and this bar
  straddles it". A break now registers once and ages; age-0 share falls to
  42.3%. Two regression tests pin it. Found because removing the retest
  requirement altogether changed the trade count by 19 out of 2,788.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **The retest was never filtering anything.** `_track_break` tested "price is
  beyond a level" rather than "price was not already beyond it", so a break
  re-registered every bar, resetting `broke_age` to 0 and clearing `retested`;
  the same call then re-set it if that bar's range straddled the zone. 81.4% of
  live breaks sat at age 0. "Break -> come back -> retest -> hold" was in
  practice a single-bar test of "price is beyond the level and this bar
  straddles it". A break now registers once and ages; age-0 share falls to
  42.3%. Two regression tests pin it. Found because removing the retest
  requirement altogether changed the trade count by 19 out of 2,788.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **The retest was never filtering anything.** `_track_break` tested "price is
  beyond a level" rather than "price was not already beyond it", so a break
  re-registered every bar, resetting `broke_age` to 0 and clearing `retested`;
  the same call then re-set it if that bar's range straddled the zone. 81.4% of
  live breaks sat at age 0. "Break -> come back -> retest -> hold" was in
  practice a single-bar test of "price is beyond the level and this bar
  straddles it". A break now registers once and ages; age-0 share falls to
  42.3%. Two regression tests pin it. Found because removing the retest
  requirement altogether changed the trade count by 19 out of 2,788.

- The first draft used the regression's own t-statistic as the trend filter. Its
  standard error assumes independent residuals, which prices violate: on a
  driftless random walk it exceeds 2 about **80%** of the time, so the filter
  admitted almost everything. Caught by a test asserting the null distribution.
  Replaced with the correctly scaled `drift_zscore`.
- The price chart's lower panel assumed a strategy exposing `macd_hist` also
  exposed `macd` and `macd_signal`, and raised `KeyError` otherwise.

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

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

- **The retest was never filtering anything.** `_track_break` tested "price is
  beyond a level" rather than "price was not already beyond it", so a break
  re-registered every bar, resetting `broke_age` to 0 and clearing `retested`;
  the same call then re-set it if that bar's range straddled the zone. 81.4% of
  live breaks sat at age 0. "Break -> come back -> retest -> hold" was in
  practice a single-bar test of "price is beyond the level and this bar
  straddles it". A break now registers once and ages; age-0 share falls to
  42.3%. Two regression tests pin it. Found because removing the retest
  requirement altogether changed the trade count by 19 out of 2,788.

- Engine re-sized held positions every bar, emitting ~590 one-share rounding
  trades on a 21-session run. It now trades only when the target exposure
  changes (regression test added).
- Round-trip PnL double-counted spread: gross PnL is now measured on pre-cost
  reference prices, with spread/slippage and commission reported separately, so
  `net_pnl` reconciles exactly with the cash change.

- **`session_drift_zscore` measures position, not direction.** It is
  `sum(r since the open)/(sigma*sqrt(n))`, so a large early move fixes its sign
  for the rest of the session. Worked example (TSLA 2025-01-30, short at 10:25,
  -324 bps): drift since the open -284 bps giving `momentum_z` -1.09, while the
  last 6 bars were **+120 bps** and the session low was 25 minutes old. The
  filter said "short" into a 25-minute rally.
- **Fixing it makes the backtest worse**: gross/trade +3.50 (session) -> +2.98
  (ewma span 12) -> +1.13 (ewma span 6). The reason is a direct slice: 19% of
  entries are taken *against* the last six bars and those earn +6.58 bps against
  +2.77 for the ones that agree. The stale indicator was accidentally producing
  counter-trend entries, and on this universe fading beats following (R04/R05:
  momentum rank IC -0.023, t = -11.2). Broken on mechanics, broken in the
  profitable direction.
- **Requiring Kronos agreement is worth +0.28 bps at t = +0.06** over all 2,750
  scored trades (agreed 45.8% hit / +3.86 bps on 33% of the book; disagreed
  45.2% / +3.58 bps). This is less damaging than R09's backtest, which showed
  +3.50 -> +1.13: the two reconcile because vetoing frees position slots and the
  replacement trades are worse. Most of R09's damage was that indirect effect,
  not the filter's own selection.
- **The higher-low structural objection is not distinguishable from noise.**
  Trading against the session structure: -1.35 bps against +0.57 with it,
  difference t = -0.37 on n = 903. Sharper on the short leg (-4.63 vs +5.31) but
  on 121 trades, and the long leg flips sign — and it contradicts the "fights
  the last six bars" slice on a larger sample.

- **The late entry in the charted TSLA loser was caused by the blackout.**
  Baseline entered 09:40 at 395.60 as price left the 400 level, before the fall
  to 386. With `no_entry_before=10:20` the first admissible bar was 10:25, 25
  minutes after the low, into the rally: -324 bps. The delay does not take the
  same trade later, it takes the reversal.
- **With the state machine corrected, entering on the break beats waiting for
  the retest**: gross +4.25 bps (t = +1.84) against +3.70 (t = +1.61), maxDD
  -6.22% against -8.35%. Still under Bonferroni for 26 trials (|t| ~ 3.2), and
  R10 §5's cost finding applies unchanged. Config default stays
  `require_retest: true`, which is what the report specifies.

- **Position sizing was anti-correlated with the edge — this is why total
  return stayed negative while per-trade expectancy was positive.**
  `weight = risk_per_trade / (stop_sigmas * sigma_H)` gives a tight stop a large
  position, so `corr(sigma, notional) = -0.897`. But the edge here grows with
  volatility: lowest-vol quintile -1.44 bps at a 38% win rate, highest-vol
  +7.45 bps at 51%. The strategy bet most on its worst trades. Equal-weighted
  mean +1.25 bps against a notional-weighted **-0.83 bps** — that gap is the
  entire discrepancy. Not a bug; the textbook rule, wrong for an edge that
  scales with volatility.
- With only the sizing changed, total return goes **-2.17% -> +4.56%**
  (Sharpe +0.41, maxDD -8.00%, gross +4.27 bps at t = +1.85). Same entries,
  exits and fills.
- **The win rate was never the problem**: 45.5% against a 43.0% breakeven,
  carried by a payoff ratio above 1.
- **It still fails on honest costs.** Equal sizing raises the share of trades in
  the first two bars from 49% to 58%, because it stops shrinking positions in
  the volatile names the open produces. At measured spreads the variant runs
  **-2.99 bps/trade**; the bar 0-1 bucket needs a half-spread under 2.25 bps
  against 4.91 measured.

### Validation

- `pytest` — 41 passed.
- Real-data run: PLTR 1-minute IEX, 2026-07-28 → 2026-08-25, 8,160 bars.
  MA(20/60) long-only, flat at 15:55, 1.5 bps impact per fill →
  +6.02% net vs +37.44% frictionless buy & hold, 95 trades, −5.47% max
  drawdown, $2,867 costs.
- No-lookahead: bars after time T do not change any fill at or before T.
- Accounting: realised trade PnL equals the equity change on a flat-ending run.
