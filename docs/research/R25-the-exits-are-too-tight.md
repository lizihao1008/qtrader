# R25 — The exits are too tight

**Date:** 2026-09-07
**Instruction:** the round-trip cost is fixed; find the breakthrough in the
algorithm.

> **Read with R27.** The result below is bought by deleting the 2-ATR
> initial-stop cap, and that trade turns 8 trades worse than −200 bps
> into 56, with the worst single trade going from −273 to −711 bps. It
> is a short-volatility payoff, not a 1.5% edge with noise around it.


**Result: the largest replicating effect in this project is not in the entry at
all. The average LOSING trade is up +34 to +39 bps before it ends at −52.
Widening the exits improves every metric in every window on the universe it was
tested on, and raises the hit rate in 9 of 9 universe-window cells — but the P&L
effect fails to replicate in 2 of 6 out-of-universe cells. It is a real
mechanism and not yet a reliable edge.**

## 1. Where the measurement pointed

Screening the strategy's own trades (book unbound, all three windows):

| | `m5_mine` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| **losers: mean gross** | −52.2 | −51.9 | −56.1 |
| **losers: mean MFE** | **+34.1** | **+35.4** | **+38.6** |
| winners: gross / MFE (kept) | 0.63 | 0.61 | 0.62 |
| corr(hold_bars, gross) | +0.59 | +0.46 | +0.56 |

Three windows, near-identical numbers. **Every average loser was meaningfully in
profit first, and gave back ~87 bps to end at −52.** Winners keep 62% of their
best. And longer holds correlate positively with the outcome, so the problem is
not holding too long.

Two hypotheses were tested first and rejected:

* **size by volatility** (R24 §1 showed mean gross rises with σ): weighting the
  same trades ∝σ gives −0.38 / +7.25 / +3.20 bps against +0.66 / +1.38 / +1.98
  equal-weighted. The sign flips on `m5_mine`. Rejected.
* **cap the give-back** (`max_giveback`, previously unused): this is the
  decisive negative result of the session — see §2.

## 2. Tightening the exit raises the hit rate and destroys the return

| `max_giveback` | hit rate (mine/val/test) | total return (mine/val/test) |
| --- | --- | --- |
| off (baseline) | 0.368 / 0.354 / 0.387 | −7.95% / −4.23% / −1.36% |
| 0.7 | 0.453 / 0.446 / 0.475 | −11.10% / −4.17% / −3.04% |
| 0.5 | 0.478 / 0.469 / 0.490 | −14.13% / −4.72% / −2.24% |
| 0.3 | **0.484 / 0.479 / 0.492** | **−14.19% / −3.89% / −5.25%** |

Hit rate rises monotonically in all three windows, by up to 13 points. Return
falls. This is R24 §1 demonstrated causally rather than by correlation:
**mechanisms that raise the hit rate here cut the right tail, and the tail is
the P&L.** It also raises the trade count (3153 → 3949): a capped trade closes
and the setup re-opens, paying the toll again.

## 3. The mirror hypothesis is the one that works

If tightening hurts, the exits were already too tight. Widening
(`trail_sigmas` 1.5 → 3.5, `stop_sigmas` 1.0 → 2.0, and **removing the 2-ATR
initial stop cap entirely**), on the ml12 universe:

| | `m5_mine` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| return | −7.95% → **−1.24%** | −4.23% → **+1.64%** | −1.36% → **+1.47%** |
| gross bps | +1.11 → **+2.55** | +1.56 → **+3.79** | +2.38 → **+3.98** |
| net bps | −1.89 → −0.45 | −1.44 → **+0.79** | −0.62 → **+0.98** |
| hit rate | 0.368 → 0.492 | 0.354 → 0.473 | 0.387 → 0.494 |
| trades | 3153 → 2029 | 2022 → 1335 | 1570 → 1058 |
| **top-5 share of gross** | 86% → **61%** | 193% → **121%** | 85% → **77%** |

Every metric improves in every window, including the two that usually move
against each other. The tail concentration *falls*, so the result depends less
on a handful of trades than the baseline did — the opposite of R24's arm.

The 2-ATR cap does most of the work. It was added as "Turtle-style initial loss
control"; on this data it closes trades before they work.

## 4. Where it fails

Applying the same three parameters, unchanged, to two universes it was **not**
swept on:

| universe | window | gross baseline → wide | return |
| --- | --- | ---: | ---: |
| SPY+QQQ (1.5 bps) | m5_mine | +2.92 → **+5.00** | +5.72% → +10.66% |
| SPY+QQQ | m5_validate | +4.01 → **+8.19** | +6.75% → +13.54% |
| SPY+QQQ | m5_test | +0.74 → **−1.24** ✗ | −1.45% → −3.83% |
| us_liquid_22 (3 bps) | m5_mine | +0.56 → **+4.88** | −12.80% → +5.31% |
| us_liquid_22 | m5_validate | +0.21 → **−1.53** ✗ | −10.14% → −8.78% |
| us_liquid_22 | m5_test | +1.39 → +1.36 ≈ | −4.35% → −2.39% |

**Hit rate rises in 9 of 9 cells** (typically +11 to +15 points) and the trade
count roughly halves everywhere — those effects are mechanical and universal.
**Gross improves in 3 of 6 out-of-universe cells, worsens in 2, is flat in 1.**

So the honest verdict: the *mechanism* is established — the exit was cutting
trades that were working — but converting it into P&L is about a coin flip
outside the universe the parameters were chosen on.

## 5. Why this is still the best direction available

Everything else measured in this project has been neutral by construction: R21
and R22 gates, R23 §2 filters, R24 conviction thresholds — all of them select
among candidates, and R18/R24 §1 show there is no candidate score with real IC
to select on. **The exit is the one lever that does not need a predictive score
to work.** It does not have to know which trades will win; it only has to stop
closing the ones that already are.

That is also why the measurement in §1 is trustworthy in a way the others were
not: it was a prediction made *before* the sweep (losers reach +35 bps MFE →
the exits are too tight → widening should help), not a maximum found by
searching.

## 6. Next

1. **Fit the exit width per symbol, inside training folds only.** A single
   `trail_sigmas` for twelve names with different volatility profiles is why §4
   is a coin flip; the σ normalisation evidently does not fully absorb it.
2. **Do not add a give-back rule, a time stop, or any hit-rate-raising exit.**
   §2 measured that family directly and it is negative in every window.
3. Do not resume entry-side filtering. Five separate attempts, all neutral.

## 7. Recorded

Three trials appended to `results/search/ledger.jsonl`, verdict PARTIAL. Six
exit arms were swept on three windows. `m5_test` now carries 7 trials,
`m5_validate` 6.
