# R29 — The wide exit with a correct momentum estimator

**Date:** 2026-09-08
**Instruction:** drop the hard stop; with a correct momentum estimate, what does
the backtest look like?

**Answer: worse, at every span tested. No fixed-span EWMA beats the `session`
estimator on the later windows, and the best of them is the longest — the one
that behaves most like the estimator it was meant to replace.**

## 1. The result

`trail_sigmas 3.5`, `stop_sigmas 2.0`, no ATR cap, no hard stop — R25/R26's
geometry unchanged. Only the momentum estimator varies.

| arm | `m5_mine` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| **wide, `session` z (R25/R26)** | −1.24% | **+1.64%** | **+1.47%** |
| wide + ewma span 3 (15 min) | −3.55% | −3.09% | −0.18% |
| wide + ewma span 6 (30 min) | −6.68% | −1.67% | −5.40% |
| wide + ewma span 12 (1 h) | −2.60% | −4.16% | −3.42% |
| wide + ewma span 24 (2 h) | −0.34% | +0.25% | −0.63% |
| tight + ewma span 6 | −8.07% | −2.85% | −3.69% |

Gross bps per trade, same order: `+2.55/+3.79/+3.98` for `session` against
`+1.73/+1.42/+2.88`, `+0.55/+2.17/−0.64`, `+2.07/+0.90/+0.70`,
`+2.88/+3.12/+2.58`.

The ordering among spans 3–12 is noise. The signal is at the ends: **the
growing-window `session` estimator beats every fixed span, and the best fixed
span is the longest one tested.** Nothing here rewards a faster read of
momentum.

Risk does not improve either: worst single trade with ewma span 6 is **−866 bps**
on `m5_validate`, against −648 for `session`.

## 2. Why this is coherent rather than surprising

R04/R05 measured intraday momentum on this data as **reliably wrong-signed** —
rank IC −0.023 at t = −11.2. That is a real, strongly significant relationship
pointing the wrong way.

`session` is not a momentum estimator. Its numerator is `log(close/open)`, so it
measures *distance from the session open* — a different quantity that happens
not to carry the wrong-signed short-horizon relationship. Replacing it with a
genuine short-horizon momentum reading moves the gate towards precisely the
statistic that was measured to be inverted, and the result degrades accordingly.
Span 24 — two hours, closest to a whole-session read — degrades least.

So R28 §1 and this note are both true at once: **`momentum_z` was mislabelled,
and correcting the label makes the strategy worse.** The name was wrong; the
quantity was doing useful work under a wrong name.

## 3. A caveat on how these arms differ

Trade counts barely move — 2029 → 2053 on `m5_mine`, 1058 → 1069 on `m5_test`,
about 1%. The estimator is therefore not gating many more or fewer candidates;
it is swapping *which* candidates fire at the margin, and with
`max_positions: 6` binding, one swap cascades (R23 §2). Count is not membership.
A 1% change in trade count coexisting with a 7-point swing in return is a
property of the book, not of the estimator.

## 3a. What the two arms actually disagree about

§3's "counts barely move" understates it. Matching the two arms' trades on
(symbol, entry time) over `m5_test`:

| | trades | gross bps | hit |
| --- | ---: | ---: | ---: |
| taken by both arms | 604 | +2.79 | 0.490 |
| **only by `session`** | **454** | **+5.94** | 0.500 |
| **only by `ewma(6)`** | **465** | **−4.91** | 0.486 |

Only **57%** of the book is shared. The estimator swaps out roughly 450 trades
worth +5.94 bps for roughly 450 worth −4.91 — an 11 bps swing on 43% of the
book, which is the whole result.

And the difference is concentrated in one direction:

| | LONG | SHORT |
| --- | --- | --- |
| `session` | n=561, **+10.18 bps** | n=497, −2.67 |
| `ewma(6)` | n=550, **+3.46 bps** | n=519, −4.81 |

**All of the strategy's gross edge is in its longs, and `session`'s longs are
three times better.**

The reason is what `session` actually gates on. `z > +0.25` under that estimator
means `log(close/open)` is meaningfully positive — **the stock is up on the
day**. So a `session`-gated long is "buy a level break in a name that is already
up on the session", which is a day-level relative-strength condition. The EWMA
version replaces it with "the last 30 minutes were up", which is short-horizon
momentum — the statistic R04/R05 measured at rank IC −0.023, t = −11.2.

This sharpens §2 from an explanation into a testable claim: the profitable
component of this rule is not momentum in any timeframe, it is **day-level
direction**, and it has been travelling under the name `momentum_z` throughout
R01–R29.

## 4. Where this leaves R25–R29

Three defects were found by reading one chart, and all three are genuine:

| defect | real? | fixing it does |
| --- | --- | --- |
| stop widened by a gap-inflated σ (R27) | yes | removes the return (R28 §4) |
| symbol goes blind while held (R27 §2) | yes | nothing (R28 §3) |
| `momentum_z` is distance-from-open (R28 §1) | yes | makes it worse (here) |

Every one of them was load-bearing. That is the clearest statement available of
what R21–R29 have measured: **the strategy's apparent profit lives in its
defects, because the signal underneath has no information** — R18 rank IC
+0.0001, R24 §1 `momentum_z` t = +2.65 / +0.04 across two windows.

There is no configuration of this rule that is simultaneously correct and
profitable, and that is not a tuning problem.

## 5. Recorded

Six arms on three windows. `momentum_estimator` stays at its `session` default;
`hard_stop_bps` and `track_breaks_while_held` stay off. No committed
configuration changes as a result of this note.
