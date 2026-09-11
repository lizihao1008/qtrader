# R33 — Auditing the trend-precursor test

**Date:** 2026-09-10
**Question:** the trend labels are hand-chosen, so the pre-trend signals may be
similar by construction. Is that a problem? Would comparing pre-trend momentum
against momentum at random times work? Is there a better method?

**Answers: (1) yes, there is a selection defect, but it is in the control pool,
not the labelling — and it did not manufacture a false positive here; (2) no,
random times would be worse than what is already built; (3) yes, and it is run
below. The binding constraint turns out to be statistical power: with 187 events
nothing below AUC 0.572 is detectable, and every effect measured is smaller than
that.**

## 1. The labelling is clean

`labels/trend_events.py` is careful in the way that matters. `sigma` at `t` uses
only returns `<= t`; every other quantity uses the **open** interval `(t, t+H]`.
The factor is `log(close[t-L] / close[t-L-N])` with `L >= 1`, so it ends at or
before `close[t-1]`. Factor and label share no bar, and `_assert_no_leakage`
checks `factor_end_time < bar_open` and `< trend_start` on every row. The
bootstrap resamples **by session**, which is the right cluster.

None of that is where the problem is.

## 2. The defect is the control pool

`_eligible_controls` removes every bar within `exclusion_minutes` (30) of **any**
event. So:

* a **trend** sample is the bar at the edge of a clean directional move;
* a **control** sample is a bar at least 30 minutes from *any* clean directional
  move — drawn, by construction, from the quiet parts of the day.

The control is selected on the outcome. Any difference between the groups is
part signal and part sampling, and the split cannot be recovered afterwards.
This is the defect worth fixing, and it is a different one from the one
suspected: the labels do not pin the pre-trend features, the *controls* do.

It did **not** create a false positive here. The existing run reports median
directional AUC **0.424** and median top-decile lift **0.82** — already null, and
several cells below 0.5.

## 3. Comparing against random times would be worse

Random bars are not matched on symbol or on time of day. Trend starts cluster
in the volatile parts of the session, so an unmatched control mostly measures
"trends happen when the market is active" — true, and useless. The existing
same-symbol, TOD ±30 matching is strictly better than the proposed alternative.

The deeper issue is the frame. A two-sample difference answers "do these
populations differ". The tradeable question is a conditional probability:
**given what I can see now, how likely is a trend to start, against the
unconditional rate?** That question has no control pool, so it cannot be gamed.

## 4. The better design, built and run

`experiments/trend_lift.py`. Every bar with a valid factor and a complete label
window is a sample; there is no control pool at all.

**A. Unconditional lift** — 106,629 bars, 187 trend starts, base rate **1 in 570**.

| | |
| --- | --- |
| AUC range across 20 (window, lead) cells | **0.448 … 0.509**, median 0.478 |
| top-decile lift | 0.53 … 1.18, median **0.88** |
| smallest AUC detectable at this n | **0.572** |

**B. What is actually elevated before a Trend Start** — nothing:

| measurement | AUC | event mean / other mean |
| --- | ---: | ---: |
| \|mom_5\| | 0.520 | **0.91** |
| realised vol 20 | 0.490 | 0.94 |
| bar range / close | 0.508 | 0.96 |
| vol ratio 20/120 | 0.484 | 0.97 |
| volume ratio 20/120 | 0.532 | 0.95 |

Every ratio is **below 1**. Momentum, volatility, range and volume are all
slightly *lower* before a trend start than at a typical bar. The sign is
consistent across five unrelated families, which is why the original test's AUCs
sat below 0.5 rather than scattering around it.

**C. Hard negatives** — the question that decides tradeability. Among bars that
already look like a breakout:

| slice | n | P(trend start) | lift |
| --- | ---: | ---: | ---: |
| all bars | 106,629 | 0.00175 | 1.00 |
| \|mom_5\| top 10% | 10,497 | 0.00133 | **0.76** |
| …then high realised vol | 9,232 | 0.00111 | 0.71 |
| …then high vol ratio | 9,210 | 0.00054 | 0.36 |

**A bar that looks like a breakout is less likely to start a trend than a bar
picked at random**, and stacking volatility filters makes it worse. This is the
same reversion R30–R32 measured on the cross-section, arriving from the
time-series side.

## 5. The anchor hypothesis, tested and rejected

A plausible objection: the Trend Start is one bar chosen from a run of
overlapping candidates, so the exact minute is arbitrary and a precursor test
anchored on it is punished by jitter. Measured: `candidate_run_length` has
median **1**, p90 3, max 6 — there is very little jitter. Relaxing the target to
"a trend starts within the next K bars" does not help:

| target | n | \|mom_5\| AUC | mom_5 | mom_20 | rvol 20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| exact bar | 187 | 0.526 | 0.469 | 0.448 | 0.490 |
| within 3 | 561 | 0.488 | 0.471 | 0.457 | 0.487 |
| within 10 | 1,870 | 0.500 | 0.486 | 0.469 | 0.493 |
| within 20 | 3,740 | 0.502 | 0.485 | 0.475 | 0.492 |

## 6. The real constraint is power

| events | AUC standard error | smallest detectable AUC |
| ---: | ---: | ---: |
| **187 (today)** | 0.037 | **0.572** |
| 500 | 0.022 | 0.544 |
| 2,000 | 0.011 | 0.522 |
| 4,000 | 0.008 | 0.516 |

One symbol over thirteen months yields 187 events. Nothing subtle is findable
at that size, whatever the design. Even a real AUC of 0.55 — which would be a
strong intraday signal — is invisible.

## 7. What to do, in order

1. **Run the collector over the full 22-symbol universe before anything else.**
   ~4,000 events takes the detectable floor from 0.572 to 0.516. This is one
   config change and it is worth more than any refinement of the test.
2. **Replace the control pool with the unconditional design** (§4A). It removes
   the last piece of researcher freedom and reports the base rate a trader
   actually faces.
3. **Stop testing momentum as the precursor.** Five families, all with ratios
   below 1, and breakout-looking bars at 0.76× the base rate. The direction of
   every measurement says trends start out of *compression*, not out of
   momentum.
4. **Test compression directly, with the detector already in hand.** The
   `market_state` consolidation state machine (ADR-0008) is causal and already
   wired into this repo via `experiments/consolidation_gate.py`. The hypothesis
   it makes testable is `P(trend start | inside a tracked box, close near an
   edge, box age > k)` against the 1-in-570 base rate. That is a precursor with
   a mechanism, and unlike momentum the sign of every measurement here points
   towards it.

## 8. Recorded

`experiments/trend_lift.py` with six tests, including a planted-signal control
(a synthetic precursor must be recovered at AUC > 0.9, so a null result means
something) and a pure-noise control. Nothing in `trend_precursor.py` or the
labels was changed; this note is an audit plus a second design, not a
replacement.
