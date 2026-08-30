# Current Task

## Goal

Implement the requested strategy and let the test decide:

> 保留 S/R 判断，加上追动量的策略，然后找到入场机会后使用 Kronos 交叉验证

Keep the ex-ante support/resistance logic from `docs/research/deep-research-report.md`,
add a momentum-continuation entry, and use Kronos as a cross-validation filter on
the entries the S/R logic proposes.

## Current State (2026-08-30)

Both standing instructions are implemented, enforced and are now the config
default: **Kronos agreement required** for every entry, and **no previous-day
K-line** may influence a decision. R11 records the cost of each.

| configuration | trades | gross | t | total return | Sharpe | hit rate |
| --- | --- | --- | --- | --- | --- | --- |
| no previous-day, no filter | 2,761 | +4.33 | +1.86 | **+4.86%** | +0.44 | 45.7% |
| **+ Kronos required (config default)** | 2,923 | +1.22 | +0.61 | **−7.49%** | −0.65 | 43.0% |

The Kronos requirement is retained on instruction, not on evidence, and the
config says so. Under R10's measured spreads both rows are negative anyway.

## Superseded state

**Complete. The test decided, and the answer is negative.** Written up in
`docs/research/R09-sr-momentum-kronos.md`.

The strategy has no measurable edge (+3.50 bps gross at t = +1.51 over 2,788
trades against a 3.00 bps round trip), Kronos has no directional information
about its entries (49.3% agreement, rank IC −0.011), and the conjunction is
monotonically worse than the strategy alone.

## Completed

- `src/qtrader/features/levels.py` — previous-day, opening-range and round-number
  levels, all ex-ante, plus ATR and the tolerance zone. 8 unit tests.
- `src/qtrader/strategies/sr_momentum.py` — break → retest → hold → momentum →
  participation → VWAP side, released by the `trend_ratchet` volatility barrier.
  Publishes a `candidate` column so an external model can score proposed entries.
  11 unit tests including a prefix-invariance (leakage) test.
- `src/qtrader/models/kronos_confirm.py` — candidate collection, batched Kronos
  scoring, parquet cache. 10 unit tests against a stub predictor, including the
  window-boundary test.
- `scripts/kronos_confirm.py`, `scripts/analyze_confirmation.py`,
  `scripts/plot_setups.py`.
- `src/qtrader/viz/setups.py` — real-price gallery showing the watched S/R
  level, the predicted direction and the Kronos forecast path per trade.
  Rendered at `results/sr_momentum_5min__m5_mine/setups.html` (20 winners,
  20 losers). 9 unit tests.
- `config/backtest/sr_momentum_5min.yaml`.
- Execution audit on `m5_mine`: all five checks PASS (5,540 fills).
- Full suite green: 196 unit + 26 integration.
- Kronos runs locally on MPS (M3 Pro): `Kronos-small` + `Tokenizer-base`,
  lookback 128, batch 128 → ~26 windows/s.

## Findings

- **Defect found and fixed by testing**: `nearest_round_levels` brackets the
  *current* close, so `price > resistance` was unsatisfiable and the entire
  round-number family was inert. The reference is now the previous bar's
  brackets — which is also the only causal version. Candidates went 5,762 →
  15,646 and the baseline changed, so the earlier baseline is void.
- Baseline `sr_momentum` on `m5_mine` (round levels live), 2,788 trades:
  gross +3.50 bps (t = +1.51), cost 3.00 bps, expectancy +0.50 bps/trade,
  hit rate 44.1%, mean hold 48 bars, turnover 1.80x/day.
  Total return −3.02%, Sharpe −0.47, maxDD −8.95%.
  Short leg gross +4.95 bps vs long +2.17 bps.
  **t = +1.51 on 2,788 trades is indistinguishable from zero.** Per-trade
  expectancy is positive while capital-weighted return is negative, so the
  losers are in the larger positions.
- Kronos score distribution over the 15,646 candidates: mean −0.046, sd 1.11,
  48.6% positive.
- **Kronos has no directional information about these entries.** 49.3%
  agreement with the realised direction at 12 bars (rank IC −0.0108, t = −1.35),
  49.1% at 48 bars (−0.0173, t = −2.16). The ">50% win rate" claim does not
  transfer to 5-minute US large caps against a 3 bps round trip.
- **The filter makes the strategy monotonically worse** — hit rate 44.1% →
  41.4% → 40.6% → 40.4% as the threshold tightens. It removes winners slightly
  faster than losers, which is what a fractionally negative IC does. The short
  leg, the only positive sleeve at +4.95 bps gross, is turned negative.
- R08's arithmetic is confirmed on both halves: `A` uninformative (t = +1.51),
  `B` uninformative (IC ≈ 0), so the conjunction is uninformative on a smaller
  sample.

## Open Issues

- **The cost model is flat and the spread is not.** 1.0 bps half-spread is
  charged at 09:35 and at 15:20 alike. Measured on 1-minute bars, the Roll
  half-spread is 4.91 bps in the first five minutes against 0.34 bps mid-day —
  14x. Roughly 3x conservative mid-day, roughly 5x optimistic at the open.
  **This inflates every result recorded in this repository**, in proportion to
  how much of its turnover sits in the first half hour (for `sr_momentum`, half
  of it). See R10.
- `m5_mine` has now carried **33 recorded trials** (Bonferroni |t| ≈ 3.3).
- **Book capacity distorts every veto filter tested** (three times now:
  R09 Kronos, R10 blackout, R11 Kronos). `max_positions=6` binds, so a veto
  frees a slot rather than removing an opportunity, and `_respect_book_limit`
  fills it with a weaker candidate. Any future filter must be evaluated as a
  *ranking* input, not only as a veto. Bonferroni at α = 0.05 puts
  the bar at |t| ~ 3.0 for anything found on it.
- Nothing in the repository has a positive expectancy net of realistic costs on
  an untouched window. That is the standing state of the research, not a bug.

## Next Actions

1. **Build a time-of-day-aware cost model.** Highest-value change in the
   repository, ahead of any signal work. It touches `backtest/costs.py`, so it
   needs regression tests, and it will re-score every recorded result — which is
   the point. Deliberately not slipped in as a side effect of R10.
2. Re-run the recorded baselines against it and correct the numbers in R09/R10.
3. Do **not** carry `sr_momentum` or the Kronos filter forward — both were
   measured and both are null, and R10 makes the baseline worse rather than
   better.
4. Run any future confirmation model through `scripts/analyze_confirmation.py`
   *before* writing a backtest. A filter with no rank IC against candidate
   outcomes cannot help one.
5. `m5_validate` and `m5_test` are still unspent for 5-minute work. Keep them
   that way until something is clearly positive on `m5_mine` under honest costs.

## Files Touched

- `src/qtrader/features/levels.py`, `src/qtrader/strategies/sr_momentum.py`,
  `src/qtrader/models/kronos_confirm.py`
- `scripts/kronos_confirm.py`, `scripts/analyze_confirmation.py`
- `config/backtest/sr_momentum_5min.yaml`
- `src/qtrader/viz/setups.py`, `scripts/plot_setups.py`
- `tests/unit/test_levels.py`, `test_sr_momentum.py`, `test_kronos_confirm.py`,
  `test_setups.py`
- `docs/research/R09-sr-momentum-kronos.md`, `docs/CHANGELOG.md`

## Results — R10, where the trades happen

49% of entries are decided on bar 1 (09:35-09:40); 76% come from round-number
levels, not previous-day levels.

| variant | trades | gross/trade | t | total return | net @ flat 3.00 | net @ measured spreads |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | 2,788 | +3.50 | +1.51 | −3.02% | +0.50 bps | **−3.09 bps** |
| no entry before 09:40 | 2,948 | +0.40 | +0.19 | −6.37% | −2.60 | — |
| no entry before 10:20 | 2,892 | −0.27 | −0.16 | −11.28% | −3.27 | — |
| no previous-day levels | 2,767 | +4.62 | +1.96 | **+0.30%** | +1.62 | **−2.06 bps** |

The one positive total return is trial 18 on a mined window and rests entirely
on the bucket the flat cost model undercharges.

**Why it performs badly** (R10 §5b): not the exit — the ratchet beats every
fixed horizon and winners capture 64% of their MFE. Entry direction is the
fault: 66% of losers never saw MFE reach half their MAE. The edge is 2
percentage points of win rate (45.3% vs 43.3% breakeven, se 0.94) before costs.
Delaying collapses the win rate to 40.7% through its own 40.9% breakeven by
**selection** — a break/retest still alive at bar 10+ is one where price failed
to run.

Galleries: `setups.html` (baseline), `setups_delay1020.html`,
`setups_noprevday.html`, each 20 winners + 20 losers with the peak marked.

**The charted TSLA loser's late entry was caused by the blackout** (R10 §5f):
baseline entered 09:40 at 395.60 before the fall to 386; with
`no_entry_before=10:20` the first admissible bar was 10:25, into the rally,
−324 bps. The delay takes the reversal, not the same trade later.

**Bug fixed: the retest was never filtering** (R10 §5g). `_track_break` tested
"price is beyond a level" rather than "was not already beyond it", so breaks
re-registered every bar — 81.4% of live breaks sat at age 0 and the retest
window never accumulated. Now registers once and ages (age-0 share 42.3%).
With it corrected, **entering on the break beats waiting**: gross +4.25 bps
(t = +1.84) vs +3.70 (t = +1.61), maxDD −6.22% vs −8.35%. Under Bonferroni for
26 trials (|t| ≈ 3.2). Config default stays `require_retest: true`.

**Root cause of "positive expectancy, negative return": position sizing**
(R10 §5h). `weight = risk_per_trade / stop_distance` means low volatility → big
position, `corr(sigma, notional) = −0.897`. But the edge grows with volatility
(lowest-vol quintile −1.44 bps at 38% win rate; highest +7.45 at 51%), so the
strategy bet most on its worst trades. Equal-weighted +1.25 bps vs
notional-weighted −0.83 bps. Added `sizing` (`risk`|`equal`); with only the size
changed, total return **−2.17% → +4.56%**, Sharpe +0.41. The win rate was never
the problem — 45.5% against a 43.0% breakeven.
**Still fails on honest costs**: −2.99 bps/trade at measured spreads, and equal
sizing raises the open-bar share from 49% to 58%.

**Three other fixes tried, all neutral-to-worse** (R10 §5c-5e):
- `momentum_estimator=ewma` fixes a real defect — `session_drift_zscore` cannot
  reverse intra-session after a large early move — but gross falls +3.50 →
  +2.98 (span 12) → +1.13 (span 6). 19% of entries fight the last six bars and
  those earn +6.58 vs +2.77, so the stale indicator was producing counter-trend
  entries and fading beats following here.
- Requiring Kronos agreement: +0.28 bps at t = +0.06 over 2,750 scored trades.
- Higher-low structure filter: t = −0.37 on n = 903.

## Results — R09, the Kronos conjunction

| variant | trades | hit rate | gross/trade | t | total return | Sharpe |
| --- | --- | --- | --- | --- | --- | --- |
| no filter | 2,788 | 44.1% | +3.50 bps | +1.51 | −3.02% | −0.47 |
| Kronos agrees (≥0) | 2,483 | 41.4% | +1.13 | +0.53 | −7.37% | −1.22 |
| Kronos ≥ 0.25σ | 2,075 | 40.6% | +1.33 | +0.62 | −7.04% | −1.28 |
| Kronos ≥ 0.50σ | 1,472 | 40.4% | +1.29 | +0.53 | −2.58% | −0.53 |

Kronos vs realised candidate outcomes: 49.3% directional agreement and rank IC
−0.0108 (t = −1.35) at 12 bars; 49.1% and −0.0173 (t = −2.16) at 48 bars.

On the 40 charted extremes Kronos agreed with the direction on 8/20 winners and
6/20 losers. Every panel shows a genuine level being broken and retested, and
winners are indistinguishable from losers before the entry — a real setup with
no conditioning power.

## Validation

- 233 tests pass (unit + integration).
- Execution audit passes all five checks on `m5_mine` (5,540 fills).
- Prefix invariance holds for the strategy; the Kronos context window is
  asserted to end at the candidate bar inclusive.
- `m5_validate` deliberately untouched — see R09 §5.

## Prior against which this is being tested

R07 measured both level families and found nothing (round-number crossings
continue at −0.0035σ vs −0.0030σ for placebos over 170,038 events; previous-day
extremes sit inside their placebo range). R04/R05 measured intraday momentum as
reliably wrong-signed here (rank IC −0.023, t = −11.2). The strategy was built
faithfully anyway because the instruction was to let results decide.
