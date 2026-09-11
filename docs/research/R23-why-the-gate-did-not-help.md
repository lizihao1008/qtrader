# R23 — Why avoiding ranges did not raise the win rate

**Date:** 2026-09-07
**Question:** a gate can only *remove* entries; if momentum persists and the
uncertain range is avoided, the win rate should rise. Why did it not?

**Answer: three reasons, and the first one is that the premise about the gate is
false — a veto is not a subtraction here. The third reason is the important one:
the cost assumption, not the signal, is what decides this strategy.**

## 1. The gate does remove bad trades. That part is right.

`m5_test`, twelve names, gate at P(hold) >= 0.60, decomposing the two arms'
trade sets by (symbol, entry time):

| | n | gross | hit rate |
| --- | ---: | ---: | ---: |
| kept by both arms | 1,537 | +2.72 bps | 0.387 |
| **vanished from baseline** | **33** | **−5.60 bps** | 0.364 |
| **new in the gated arm** | **18** | +2.97 bps | 0.500 |

The 33 trades the gate refused really were bad. Hit rate did rise, 0.3866 →
0.3884. The intuition is sound as far as it goes.

## 2. But the gated arm is not a subset — the book substitutes

Eighteen trades appear that the baseline never took. `max_positions: 6` and the
book is full on 26% of bars, so refusing entry A frees the slot for entry B.
**Eleven of the eighteen are the same setup entered later the same session**,
and those late entries earned **−27.7 bps** against the baseline trade they
replaced (median −30.6). The veto postponed the move instead of cancelling it —
precisely the failure the opening *clock* gate was written to avoid, which
consumes setups rather than queueing them.

Making the veto consume the setup too, matching the clock's semantics, was the
obvious fix. **It measured worse:** gross +2.583 → +2.373 bps, and new trades
went **18 → 59**, not down. Freeing the slot earlier lets the book admit more
substitutes. Neither reading of a veto is defensible on the evidence, so
`veto_consumes_setup` exists as a switch and defaults to the behaviour R22's
published numbers were measured with.

**With the book unbound** (`max_positions: 12`, full on 0.2% of bars) the gate's
entire effect collapses:

| | trades | hit | gross | net |
| --- | ---: | ---: | ---: | ---: |
| baseline | 1,736 | 0.384 | +1.781 | −1.219 |
| gated | 1,717 | 0.384 | +1.839 | −1.162 |

Nineteen trades removed out of 1,736, +0.06 bps, hit rate unchanged to three
decimals. Everything visible at `max_positions: 6` was substitution. This is
R17's finding reached from a new direction: **the book, not the filter, decides
which trades happen.**

## 3. The premise about momentum does not hold either

Strategy-independent: rank IC of the EWMA momentum z against the forward return,
split by the detector's state. No entries, no book, no costs.

| horizon | `m5_validate` inside / outside | `m5_test` inside / outside |
| --- | ---: | ---: |
| 3 bars | +0.0005 / **−0.0211** | −0.0059 / −0.0082 |
| 6 bars | −0.0030 / **−0.0268** | −0.0094 / −0.0055 |
| 12 bars | −0.0039 / **−0.0273** | −0.0071 / **+0.0128** |
| 24 bars | −0.0018 / **−0.0271** | −0.0103 / **+0.0158** |

On `m5_validate` momentum is *more* wrong-signed outside a range than inside it —
the ranges are where it is merely useless. On `m5_test` the difference has the
opposite sign at the longer horizons. **The sign of the effect flips between
windows**, and every |IC| is under 0.03. There is no stable "momentum continues
better outside ranges" to build on.

## 4. Two improvements tested before recommending. Both fail.

**Trade mid-session only** — costs concentrate at the open (R10), so restricting
entries should help:

| | `m5_validate` | `m5_test` |
| --- | ---: | ---: |
| all day (09:35–15:00) | −4.23% | −1.36% |
| 10:30–15:00 | −1.75% | −7.34% |
| **11:00–14:00** | **+5.32%** (gross +7.06 bps) | **−3.13%** (gross −0.04) |

**Range edges as levels** instead of as a veto — the detector's frozen
`range_high`/`range_low` are better-defined than the round numbers R07 found
inert. Mean forward 12-bar move in the break direction:

| | `m5_validate` | `m5_test` |
| --- | ---: | ---: |
| break of a range edge | **+2.64 bps** (t = +3.38, n=7,577) | −0.16 bps (t = −0.24) |
| break of a round $1 | −0.19 bps (t = −0.36) | +0.03 bps (t = +0.06) |

Both look like discoveries in one window and vanish in the other. Either would
have been reported as a finding had only one split been run.

## 5. The one thing that actually decides this

Total return against the round-trip cost assumption, everything else unchanged:

| split | gross | 0.50 bps | 1.00 | 1.50 | 2.00 | **3.00 (assumed)** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `m5_validate` | +1.56 | +2.88% | +1.43% | −0.02% | −1.40% | **−4.23%** |
| `m5_test` | +2.38 | +4.34% | +3.26% | +2.09% | +0.99% | **−1.36%** |

**The strategy is positive in both windows at 1.00 bps and negative in both at
3.00.** Break-even is ~1.5 bps on `m5_validate` and ~2.3 on `m5_test` — and
these are the twelve most liquid mega-caps in the store. A flat 3.00 bps is
$0.06 round-trip on a $200 stock; AAPL's midday spread is a penny. C00 §3b
records the flat model as ~5x *optimistic* at the open; nothing has established
what it is at midday, where it is plausibly several times too *pessimistic*.

Every gate result in R21 and R22, and every arm above, was scored against a
number with more uncertainty in it than the effects being measured.

## 6. Recommendations, in order

1. **Build the time-of-day cost model in `backtest/costs.py`.** It is no longer
   housekeeping: the sign of the entire strategy is inside the error bar of the
   current assumption. Use the Roll estimator already implemented, fit per
   minute-of-session, and re-run R19–R23 against it. This is the only change on
   this list whose value does not depend on finding a signal first.
2. **Stop testing filters.** R21, R22 and §2 above measured gates, vetoes and
   exits as neutral once the book is unbound. Any further veto gets scored on a
   reshuffled trade set, not on the trades it removed.
3. **Unbind the book in every experiment** (`max_positions` >= universe size).
   Otherwise the measurement is of substitution, not of the hypothesis.
4. **Two windows, always, before anything is called a result.** Both candidate
   improvements in §4 passed one split convincingly and failed the other.
5. If `market_state` is to be used again, its value here is as a **level
   source**, not a filter — but §4 shows even that does not replicate yet, so it
   needs a target that predicts trade outcomes rather than box persistence.
