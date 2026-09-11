# R21 — Standing aside inside a range

**Date:** 2026-09-07
**Instruction:** call the consolidation (横盘) detector in
`/Users/zihao/work/market_state`; do nothing while price is inside a range, and
only confirm entries with momentum once it is outside. Measure the return.
**Window:** `last_year` split, SPY + QQQ, 1.50 bps per round trip.

**Result: the gate has no discriminating power. It changes the return only by
changing how much the strategy trades, and the sign of its apparent selection
flips between the 5-minute and 1-minute decision grids over the same data.**

`last_year` straddles `m5_validate` and `m5_test` (R20 §note). Nothing here is
out-of-sample evidence.

## 1. What was wired up

`market_state.structure.consolidation.detect_consolidation` is a causal state
machine: `min_bars: 9` narrow (`<= 2.5 ATR`), directionless (Kaufman efficiency
`<= 0.35`) 5-minute bars seed a range with frozen edges; two consecutive closes
beyond an edge plus a `0.15 ATR` buffer confirm the break. Its own repository
pins truncation and perturbation invariance, which is why it was imported rather
than reimplemented.

It marks **42.3%** of bars as in-range on both symbols.

`SRMomentumStrategy` gained an opaque `entry_veto` frame — `timestamp x symbol`
booleans that refuse a *new* position. The strategy does not know what a range
is; the coupling lives entirely in `experiments/consolidation_gate.py`. The
veto refuses openings only: an open position is still managed and exited by the
normal rules, because forcing a flat on a state change is a different
experiment.

## 2. The three arms

Same bars, same panel loaded once; the only difference is the gate.

**5-minute decision grid** (the detector's native grid):

| arm | total return | trades | gross bps (wtd) | net bps (wtd) | time in market |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | −4.77% | 711 | −0.12 | −1.62 | 72.3% |
| **no entry in range** | **−4.63%** | 655 | **−0.20** | −1.70 | 69.3% |
| only entry in range | −0.71% | 331 | +0.99 | −0.51 | 37.4% |

**1-minute decision grid** (mask carried down with `align_to_fine`):

| arm | total return | trades | gross bps (wtd) | net bps (wtd) | time in market |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | −1.59% | 303 | +0.26 | −1.24 | 24.2% |
| **no entry in range** | **−1.01%** | 202 | +0.31 | −1.19 | 16.8% |
| only entry in range | −1.95% | 145 | −1.70 | −3.20 | 10.8% |

The instructed gate improves the total return on both grids. On neither does it
improve the **gross edge per trade** by more than 0.08 bps, against a 1.50 bps
toll. Scaling the baseline loss by the surviving trade count predicts the gated
result almost exactly on the 1-minute grid (−1.59% × 202/303 = −1.06% vs −1.01%
measured). **The gate is a way of trading less, not a way of trading better.**

## 3. The control says there is nothing to select on

If the detector separated good trades from bad, the gate and its complement
would have to move the gross edge in opposite directions. Scoring the
**baseline's own trades** by the detector's state at their decision bar — same
trade population, only the label differs, so none of the book-sequencing
confounds of R17 apply:

| decision bar | 5-minute grid | 1-minute grid |
| --- | ---: | ---: |
| inside a range | **+2.57 bps** (n=105) | **−0.52 bps** (n=119) |
| outside | −0.52 bps (n=606) | +0.76 bps (n=184) |
| Welch difference | +3.09 bps, t = +0.77 | −1.28 bps, t = −0.46 |
| session-clustered permutation | **p = 0.645** | **p = 0.763** |

The two grids disagree about the *sign*, and neither difference survives a
permutation test that shuffles the in-range label within each session (so the
null keeps the clustering of trades inside days). This is what no effect looks
like when it is measured twice.

The `only entry in range` arm on the 5-minute grid (−0.71%, gross +0.99 bps) is
the most attractive number on this page and is the same statistic as the
p = 0.645 cell. It should not be traded on.

## 4. Why this was the likely outcome

The gate is a filter, and C00 §3a records what filters do here: a veto is not a
subtraction. It removes trades without changing the quality of the ones it
leaves, because there is no candidate score with real information content to
concentrate — R18 measured the reversal statistic at rank IC +0.0001, R04/R05
measured intraday momentum as reliably wrong-signed.

A range detector answers "is price going nowhere **now**". The entry rule
already requires a level break with momentum, RVOL and VWAP agreement, which is
close to the negation of "going nowhere". The 42.3% of bars it marks overlap the
bars the entry rule was never going to fire on anyway, which is why only 8% of
5-minute trades were removed.

## 5. What would change the answer

Nothing in the exit or filtering layer. The gross edge across every arm here
spans −1.70 to +0.99 bps against a 1.50 bps toll, and no arrangement of gates
moves it outside that band. The binding constraint remains the one R19/R20
identified: no candidate score with measurable IC, and a cost model that is flat
when real spreads are not.
