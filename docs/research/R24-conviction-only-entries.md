# R24 — Only the most certain setups

**Date:** 2026-09-07
**Instruction:** stop filling the book. Enter only when the signal is most
certain; a whole day with no position is acceptable.

**Result: the instruction works mechanically — 95% fewer trades, most days flat,
hit rate up in every window — but it does not produce an edge. No arm is
positive in all three chronological windows, and the one that looks best loses
money on the largest window, which is the one it was not selected on.**

## 1. Is there anything to rank conviction by?

Screening all 19 setup features the strategy already exposes against gross
return per trade, book unbound (`max_positions: 12`), on both later windows.
Bonferroni threshold for 19 features: |t| = 3.01.

Ten features clear it in **both** windows with the same sign. The strongest:

| feature | IC validate | IC test | worst \|t\| |
| --- | ---: | ---: | ---: |
| `horizon_sigma_bps` | −0.220 | −0.179 | 7.55 |
| `initial_stop_bps` | −0.208 | −0.166 | 6.99 |
| `bar_range_bps` | −0.182 | −0.141 | 5.92 |
| `minute_of_session` | +0.144 | +0.105 | 4.41 |
| … | | | |
| **`momentum_z`** | **+0.056** | **+0.001** | **0.04** |
| **`abs_momentum_z`** | −0.007 | +0.047 | 0.31 |

The strategy's own signal ranks worst of all nineteen — R18 confirmed from a new
direction. What does rank is volatility and size.

**But the rank IC and the mean disagree, and the mean is what compounds.**
Mean gross by `horizon_sigma_bps` quintile:

| | Q1 (low σ) | Q2 | Q3 | Q4 | Q5 (high σ) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `m5_validate` | −1.78 | −0.01 | −8.37 | +1.26 | **+15.78** |
| `m5_test` | +0.70 | +1.65 | −4.51 | +6.76 | **+5.28** |

Negative rank IC, rising mean. High-σ setups lose more often *and* earn more on
average: more losers, much bigger winners. **Selecting for a high hit rate here
selects against expected value.**

## 2. The instruction, swept

Total return / trade count, all three windows:

| arm | `m5_mine` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| baseline | 3153 / **−7.95%** | 2022 / −4.23% | 1570 / −1.36% |
| z≥0.5 | 3615 / −6.08% | 2313 / −1.03% | 1804 / +2.59% |
| z≥1.5 | 2387 / −10.88% | 1567 / −5.66% | 1171 / +1.08% |
| z≥1.0, rvol≥1.5 | 2598 / −2.46% | 1606 / −8.86% | 1186 / −1.01% |
| z≥1.5, rvol≥2.0 | 1421 / −4.21% | 855 / −6.42% | 554 / −2.25% |
| z1.5 rvol2 accept3 | 352 / −1.06% | 180 / −0.16% | 93 / **+0.03%** |
| **z1.5 rvol2 accept3, 1 book** | 244 / **−3.83%** | 138 / **+2.99%** | 72 / **+3.65%** |

**Nothing is positive in all three.** The tightest arm — the one that best
matches the instruction — is the most attractive on the two windows it was
swept on and the worst-but-one on `m5_mine`, which is the largest window
(3,153 baseline trades) and the one it was not selected on.

## 3. Trying to kill the attractive arm

| | `m5_mine` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| trades | 244 | 138 | 72 |
| days traded | 207 | 117 | **65 of 162** |
| hit rate | 0.361 | 0.391 | **0.458** |
| gross mean | +1.53 | +5.70 | +8.79 |
| naive t | +0.29 | +0.78 | +0.99 |
| **clustered 95% CI** | **[−8.6, +11.7]** | **[−7.7, +20.8]** | **[−6.6, +25.4]** |
| P(bootstrap mean ≤ 0) | 0.388 | 0.223 | 0.148 |
| **top 5 trades as share of total gross** | **379%** | **178%** | **143%** |

Every confidence interval contains zero. And in every window the five best
trades carry more than the entire result: **remove five trades and all three
windows are negative.** That is not an edge, it is a handful of tail events.

The hit-rate rise is real and replicates (0.354 → 0.391, 0.387 → 0.458). It is
also exactly what §1 predicts is the wrong thing to optimise.

## 4. What the filter actually did

Compare the loss removed with the trades removed, `z1.5 rvol2 accept3`:

| | trades kept | loss kept |
| --- | ---: | ---: |
| `m5_mine` | 11.2% | 13.3% |
| `m5_validate` | 8.9% | 3.8% |
| `m5_test` | 5.9% | — (crosses zero) |

Cutting ~90% of the trades removes ~90% of the loss. That is the signature of a
per-trade expectation that is a small negative **constant** — a toll, not a
signal — and it is the third independent confirmation of R23 §5: on this
universe the loss *is* the cost.

**This is genuinely useful as a risk decision**: `z1.5 rvol2 accept3` turns
−7.95% / −4.23% / −1.36% into −1.06% / −0.16% / +0.03%. It stops the bleeding in
every window. It does not start making money in any of them.

## 5. What this says about the request

"Only the most certain" is achievable and it is not where the money is. The
per-trade distribution has a median of −18 to −6 bps and a positive mean — the
entire P&L lives in a tail of a few trades per window. Any rule that raises the
hit rate trims that tail, which is why §1's ten replicating features rank
outcomes in the opposite direction from the mean.

If the aim is **not to lose**, trade far less: §4's arm is flat in all three
windows and that result is robust because it is arithmetic, not signal.
If the aim is **to make money**, filtering cannot get there, because there is
nothing with real IC to filter on.

## 6. Recommendation is unchanged, and now better supported

R23 §5 showed this strategy is positive in both later windows at a 1.00 bps
round trip and negative in all at 3.00. §4 above shows the loss is proportional
to trade count — i.e. it *is* the toll. Two independent routes to the same
place: **build the time-of-day cost model in `backtest/costs.py` before any
further signal or filter work.** If real mid-session cost on twelve mega-caps is
nearer 1 bps than 3, the correct action is the opposite of trading less.

## 7. Recorded

Three trials appended to `results/search/ledger.jsonl`, all REJECTED. Seven arms
were swept on three windows; `m5_test` now carries 6 trials and `m5_validate` 5.
Any future claim from these splits carries that burden.
