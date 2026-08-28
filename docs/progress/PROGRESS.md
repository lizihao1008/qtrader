# Current Task

## Goal

Operate falsification-first: reject weak hypotheses on paper before implementing
them, rather than after backtesting them. Ideas must pass
[the gate](../research/GATE.md) before any strategy code is written.

## Current State

Research mode changed (ADR-0006). Applied honestly, the gate rejects
**everything currently in the repository**:

| strategy | verdict |
| --- | --- |
| `ma_cross` | not a strategy — plumbing fixture only |
| `trend_ratchet` | rejected: no mechanism at intraday horizons, IC wrong-signed |
| `cross_sectional_residual` | rejected on mechanism: the effect pays the liquidity *provider*, and this project is a *taker* |
| volatility forecasting | not an alpha — infrastructure for sizing and stops |

The prior search is complete, with a negative result.

**On this universe, at these horizons, with IEX data and realistic costs, the
current design has no reliable positive expectancy.** Nine backtests, ~200
measured cells and one out-of-sample confirmation: the best candidate was
+1.69 bps gross per trade (t = +1.78) on `m5_mine` against 2.4-3.0 bps of cost,
and +0.23 bps (t = +0.19) on `m5_validate`. `m5_test` was never run — there was
no candidate worth spending it on, so the final holdout stays clean.

Two backtester bugs were found and fixed along the way, both by auditing rather
than by reading code (see Findings).

Full reasoning: [docs/research/R04](../research/R04-5min-search.md).

## Completed

- 5-minute dataset (2024-01 -> 2026-08, 664 sessions, 1.56M bars), the timeframe
  audit in `config/backtest/trend_ratchet_5min.yaml`, and the `m5_*` splits fixed
  before any 5-minute result was seen.
- `scripts/audit_execution.py` — fill timing, prices inside the traded range,
  adverse spread, silent-bar fills, and end-to-end look-ahead, run on real data.
- `analysis/attribution.py` — attributes a result to signal / holding period /
  frequency / cost by arithmetic on `net = n * (gross - cost)`.
- `experiments/ledger.py` + `scripts/search.py` — one command per hypothesis,
  failures recorded, so the multiple-testing burden is a fact not a memory.
- `CrossSectionalResidualStrategy.allow_long` and `max_hold_intervals`.
- `features/trend.py` — `session_drift_zscore`: drift since the open, window
  grows with the day, exactly N(0,1) with no parameter. The only estimator that
  resolves a slow sustained move. Now the default.
- `trend_ratchet` entry is a **state** asked every bar, not a MACD crossing
  event; `exit_on_opposite_cross` is off (it was closing 81% of positions);
  `trend_z_reset` adds hysteresis on both the exit and the re-entry.
- `features/trend.py` — `drift_zscore`, the trend statistic with a null that
  holds on price data. The first draft used the regression t-statistic; a test
  showed it fires on 80% of driftless random walks, so the scale was rederived
  under the random-walk null and verified by simulation.
- `strategies/trend_ratchet.py` — MACD trigger + trend authorisation +
  fixed-fractional risk sizing + monotone exit barrier. Path-dependent, so it
  runs its own bar loop (ADR-0005).
- `analysis/diagnostics.py` — `diagnose()` and `Diagnosis`: config (or an
  executed run) in, episodes + screens + report out.
- `analysis/features.py` — universal market features every strategy is screened
  against, merged with whatever a strategy declares.
- `Strategy.setup_features` hook (optional) and `StrategySignals.stack`;
  implemented for both existing strategies with scale-free quantities.
- `Episodes` records which columns are setup vs outcome and carries its own
  `.screen()` / `.contrast()` / `.profile()` / `.threshold()`.
- `config/splits.yaml` + `experiments/splits.py` — named contiguous evaluation
  windows with purpose strings (ADR-0004). `mine` / `validate` / `burned`.
- `analysis/episodes.py` — one episode per round trip: K-line window from 60
  bars before entry to exit, 16 setup features read at the **decision** bar,
  outcome as gross / net / cost / MFE / MAE. Saved as parquet.
- `analysis/conditions.py` — feature screening (Spearman, quantile profiles,
  monotonicity), winner-vs-loser contrast, and a Bonferroni threshold reported
  next to every screen.
- `viz/episodes.py` — mean oriented path chart (winners vs losers vs all, with
  the surviving-episode count and truncation at 20% survival), excursion
  summary, and a best/worst candlestick gallery normalised to bps-from-entry
  with shorts flipped.
- `scripts/analyze_episodes.py`; `--split` and `--set` on `run_backtest.py` and
  `sweep.py`; `RunConfig.with_overrides`.
- `max_abs_zscore` on the strategy (default now 1.5, documented as a
  band-limited hypothesis, not validated alpha).

## Findings

### 5-minute search (R04) — negative, with five durable results

1. **Trend following is reliably wrong on this universe** — rank IC −0.010 at
   t = −4.7. Not merely unprofitable: systematically the wrong sign.
2. **Most of the reversal signal is untradable.** Its headline IC of +0.041 is
   92% bid-ask bounce and dies at one bar of implementation lag. The 12-bar
   residual version keeps 73% and is flat across lags 1-3, so it is real.
3. **What survives is one-sided.** Shorting the stretched decile carries it
   (+3.77 bps, t = +2.91); the long leg is +0.85 bps at t = +0.86.
4. **The renewal trap cost more than the signal was worth.** Renewing a position
   because the name is still extreme renews exactly the trades whose thesis has
   been falsified. Fixing it moved gross by +2.1 bps — more than the entire
   remaining edge.
5. **Cost binds and the model is not conservative.** Roll on 1-minute bars
   implies a 0.68 bps half-spread; breakeven for the best candidate needs 0.84
   bps per fill including slippage.

Bugs fixed: fills were possible on bars where nothing traded (the engine gated
on the liquidity mask at the decision bar, not on a print at the execution bar);
and the pipeline stored in-progress bars (a partial 5-minute bar was saved at
close 313.275 / volume 3,264 when the settled bar was 313.685 / 7,018).

### trend_ratchet redesign (R03)

- Three faults, not one: a fixed span cannot see a slow move; an event trigger
  cannot catch a state (0 of 30 crossings on the two sessions in question
  coincided with a significant trend); and the opposite-crossing exit was
  closing 81% of positions after ~10 bars.
- USAR's three-hour decline was **correctly** skipped: −0.73 sigma over the
  session, which a random walk of its volatility produces 47% of the time.
  BHVN's +18.5% was +2.27 sigma and is now caught.

### trend_ratchet, first version (R02)

- Risk geometry does exactly what was asked: winners +26.9 bps held 26 bars with
  mean MAE −8.0; losers −19.0 bps held 13 bars. Payoff ratio **1.42**, so
  breakeven needs a **41.3%** hit rate. Actual 39.3%. It misses by ~2 points of
  hit rate before costs, and costs add 3.0 bps per round trip.
- `mine` +0.08% net / `validate` −0.40% net. Turnover 0.6–0.8x per day, an order
  of magnitude below the cross-sectional strategy — the ratchet holds.
- `abs_trend_zscore` was the only monotone screen result (t = +2.46 vs a bar of
  2.99) and did **not** replicate on `validate`. Per-trade edge by threshold:
  `mine` −0.90 → +2.86 → +5.20 bps; `validate` +1.36 → −0.85 → +2.80. No
  gradient, no t above 1.3 anywhere.
- Rank IC of the trend z-score against forward returns is **negative** at every
  horizon (−0.007 to −0.005): these names revert over 5–30 minutes rather than
  continue. An argument against 1-minute trend following on this universe, not
  against the machinery.

### cross_sectional_residual (R01)

Full detail in R01. In short:

- **Entry/exit rules**: rank the cross-section by sector-residual return
  z-scored across eligible names, long the top 3 / short the bottom 3 beyond
  ±1σ, freeze the book until the next rebalance, exit only when a name drops out
  of the selection or at the 15:50 flatten. **Every exit is a clock event.**
- **Failure shape**: winners and losers have statistically identical setups
  (−60 bars: +30.9 vs +34.8 bps oriented). After entry the mean of all episodes
  is flat (−3 bps at +120 bars) while winners and losers fan symmetrically to
  ±69. 72% of losers were in profit at some point; 70% of winners were underwater.
  Realised PnL is where the noise landed at a clock-driven exit.
- **Conditional screen**: nothing clears |t| = 2.96 on `mine`. The only
  candidate, `abs_score`, points against the strategy (top conviction bin −7.2
  bps vs +6.2 for the second bin) — extreme residual moves continue rather than
  revert.
- **Confirmation**: the episode-level `abs_score` gradient **did not replicate**
  on `validate`. The backtest-level ordering did — uncapped is the worst row in
  both windows — but the level does not, and the best `validate` result is
  break-even (gross +$3,774 vs $3,826 of costs).

## Open Issues

- `validate` is spent for this strategy. There is no clean window left for the
  current `lookback`/`rebalance_bars`; `burned` cannot test them.
- Holding period is partly an outcome, so "long holds lose −88 bps" cannot be
  read as a setup effect. The absence of a maximum holding period is still a
  design gap.
- IEX bars carry a small share of consolidated volume; a microstructure-adjacent
  claim is being tested on a thin tape. This caps what any model can prove here.
- Shorts still have no borrow cost or locate model.
- Episode extraction loops over trades in Python (~4 s for 1,700 trades). Fine
  now; would need vectorising at 10x the trade count.

## Findings addendum — R05, why trends exist without paying

Tested directly, since it decides whether any momentum work is worth doing here:

- 2-sigma/12-bar moves occur on 8.17% of bars against 4.65% for a matched
  Gaussian walk — **1.76x**, so sustained trends genuinely are more common than
  chance.
- Shuffling the order of the same returns within each day gives **15.05%**, so
  real/shuffled = **0.543**. Fat tails and volatility clustering explain more
  than the whole excess; the time ordering suppresses trends. Directional serial
  dependence is negative, agreeing with R04's rank IC by a different method.
- Onset separation: |t| up to 9.3 but **AUC 0.466-0.528**. Significant, and a
  coin flip.
- Momentum IC negative in every volatility regime (t −0.3 to −11.2).
- **Volatility IC +0.67 vs direction IC −0.023.** Magnitude is predictable,
  direction is not. That is the asymmetry behind the whole result.

## What remains with the current data (R06)

Assessed against the gate; only one candidate survives and it is weak:

| candidate | verdict |
| --- | --- |
| order flow from `trade_count` | **rejected** — flow cannot be signed from bar data, and IEX is 2.1% of AAPL's tape at a 76-share median trade |
| `vwap` | rejected — a transform of prices already tested |
| ETF vs constituent dislocation | rejected — microsecond game, wrong side, unmeasurable at 2% coverage |
| sector-ETF lead-lag | rejected — closed well inside one 5-minute bar |
| overnight vs intraday split | **marginal** — passes mechanism/side/cost, fails incremental information. A known risk premium, not an alpha |

**With this data there is essentially nothing worth building.** That is a
statement about the venue, not about idea generation.

## Next Actions

Not more parameters — the search above exhausted that. Three things would change
the arithmetic:

1. **A better data source.** IEX carries a small share of the tape and every
   volume- and microstructure-adjacent measurement inherits that. SIP data would
   make the same tests mean something different.
2. **A daily horizon.** Everything tested lives inside a session and pays ~2.4
   bps per round trip; a daily-horizon version pays the same over twenty times
   the holding period. The overnight cells hinted at more but were not
   significant once overlap was removed, and were never tested on daily data.
3. **A genuinely different input.** Every signal tried was a function of past
   returns. The measurement framework is signal-agnostic and would give a
   straight answer about something else.
0. **Move to daily-horizon cross-sectional US equity** (R06 recommendation).
   The only change that attacks the binding constraint rather than working
   around it: cost stays ~2.4 bps per round trip while holding goes from an hour
   to weeks, latency stops mattering, and breadth rises ~50x. The pipeline,
   splits, attribution, shuffle tests and ledger all carry over unchanged.
   If the interest is specifically trend following, its honest home is futures —
   R04/R05's negative result is venue-specific and does not transfer.
4. **Trade the thing that is actually predictable.** Volatility forecasts at
   IC +0.67 are the strongest signal found anywhere in this project. They cannot
   be harvested by buying and selling stock, which needs direction, but they are
   what options/variance trades exist for — and nothing in the current design
   even uses them for position sizing or stop distance, where a trailing
   estimate is currently doing a forecast's job.

Superseded by the above:

1. ~~Re-run `trend_ratchet` on 5-minute and 15-minute bars.~~ The negative
   short-horizon IC and the 3 bps cost floor both point the same way, and only
   the config's `timeframe` needs to change. This is the single highest-value
   next experiment.
2. **Judge any future entry signal against 41.3%.** The barrier framework makes
   the requirement explicit, so a candidate can be rejected before a full
   backtest.
3. **Give the cross-sectional strategy the same exits.** Its MFE/MAE are ~1.5x
   the realised return and every exit is a clock event; `trend_ratchet`'s
   barrier is the obvious replacement and now exists.
4. **Acquire more data before scoring anything else.** Extend the dataset
   backwards so new splits can be cut without redefining the existing ones.
5. **Change the target, not the model.** A supervised model on these features
   would learn the same nothing; the useful M2 experiment moves to a longer
   horizon or triple-barrier labels.
6. Add walk-forward evaluation so a result is scored across rolling windows.
7. Model borrow costs before any short-heavy result is taken seriously.

## Files Touched

`config/splits.yaml`, `config/backtest/xsec_reversion*.yaml`,
`src/qtrader/{analysis,experiments,strategies,viz,config}.py`, `scripts/*`,
`tests/**`, `docs/**`.

## Validation

- `pytest` — 148 passed, including: the identical call diagnoses `ma_cross` and
  `cross_sectional_residual` with no analysis-side change; a strategy declaring
  nothing is still screened; a strategy feature wins a name clash while the
  universal one is kept; universal features are causal; a planted effect must be
  found and pure noise rejected; setup features are read at the decision bar;
  excursions bracket the realised return; episodes round-trip through disk with
  their column roles.
- Trend statistic verified by simulation: standard normal under a driftless
  random walk, and the closed-form slope scale matches simulated walks to 3%.
- Verified by hand on `ma_cross --split burned`: its own `ma_gap_bps` and
  `macd_hist_bps` are screened, and cross-sectional features drop out by
  themselves on a one-symbol universe.
- All four `xsec_reversion` analyses regenerated; R01's conclusion unchanged.
