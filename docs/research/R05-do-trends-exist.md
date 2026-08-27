# R05 — Do sustained trends exist, and can their onset be detected?

**Date:** 2026-08-26 · **Data:** 5-minute IEX bars, `m5_mine` (2024-01 → 2025-04, 22 names)

## The question

The proposal: extract sustained up/down trends, look at what the data was doing
before and at their onset, and test whether that differs from random bars. The
premise offered with it: *momentum must have positive expectancy at small scale,
otherwise sustained runs would not exist.*

The premise contains a gap worth naming, because it decides what the experiment
can show. **A driftless random walk produces long directional runs constantly.**
Their existence is a property of the process, not evidence that direction is
predictable. So "trends exist" and "chasing them pays" are independent claims,
and only the second is a trading proposition.

Both were tested.

## 1. Trends ARE more common than chance — the intuition is half right

Defining a trend as a bar from which price moves at least 2 sigma over the next
12 bars, on 447,678 observable name-bars:

| series | share of bars starting a trend |
| --- | --- |
| **real market** | **8.17%** |
| Gaussian random walk, same smoothed sigma | 4.65% |

The market produces **1.76x** as many sustained trends as a matched random walk.
That part of the intuition is correct and is not a small effect.

## 2. But the cause is magnitude, not direction

The Gaussian null lacks two things the market has: fat tails and volatility
clustering. Both inflate the count of large moves without any directional
predictability whatsoever. Shuffling the *order* of the real returns within each
day preserves both exactly — the same returns, the same day's realised
volatility, the same tails — while destroying every trace of time ordering:

| series | rate | vs Gaussian |
| --- | --- | --- |
| Gaussian walk | 4.65% | 1.00x |
| **real returns, order shuffled within each day** | **15.05%** | **3.23x** |
| real market | 8.17% | 1.76x |

**Real / shuffled = 0.543.** The actual ordering produces *less than half* as
many sustained trends as random ordering of the very same returns. Fat tails and
clustering alone would give 3.23x; the market gives 1.76x. The time ordering
**suppresses** trends.

That is direct evidence of *negative* directional serial dependence — mean
reversion — arrived at by a completely different method than the rank-IC work in
R04, and agreeing with it.

## 3. Onset is detectable, and the detection is worthless

Comparing 17,939 up-onsets against 18,463 down-onsets on everything observable
at or before the bar:

| feature | mean at up-onset | mean at down-onset | Welch t | **AUC** |
| --- | --- | --- | --- | --- |
| stock volatility (bps) | 13.66 | 14.38 | −9.32 | 0.466 |
| beta to market | 0.835 | 0.903 | −9.11 | 0.473 |
| bar range (bps) | 11.99 | 12.81 | −8.02 | 0.475 |
| VWAP distance (bps) | +1.81 | −2.69 | +7.61 | **0.528** |
| trailing return (bps) | +1.96 | −3.68 | +7.25 | 0.521 |
| trailing return, 12 bars, in sigma | +0.075 | −0.022 | +4.30 | 0.517 |
| relative volume | 1.355 | 1.376 | −1.89 | 0.489 |

Several clear the Bonferroni bar of |t| = 3.0. **And every AUC is between 0.466
and 0.528, where 0.500 is a coin flip.** The t-statistics are large only because
each group has ~18,000 members; the effect sizes are negligible.

Note the sign: trailing return *is* higher before an up-onset (+1.96 vs −3.68).
Momentum points the right way here. It is simply worth an AUC of 0.521.

There is also a second problem with using it. This separation is measured
*conditional on a 2-sigma move actually occurring* — information not available
at the bar. To trade it you would first have to know a big move was coming, then
call its direction at 52% accuracy.

## 4. Conditioning on volatility does not rescue it

If onset were detectable, momentum should work when a move is most likely.
Momentum rank IC (not negated, entered one bar late), by regime:

| regime | lb3/h12 | lb6/h12 | lb12/h12 |
| --- | --- | --- | --- |
| all bars | −0.0140 (−7.2) | −0.0194 (−9.7) | −0.0229 (−11.2) |
| own volatility in the top quintile | −0.0095 (−2.2) | −0.0202 (−4.7) | −0.0295 (−6.7) |
| own volatility in the bottom quintile | −0.0257 (−4.8) | −0.0351 (−6.5) | −0.0334 (−6.3) |
| cross-sectionally most volatile | −0.0131 (−3.5) | −0.0279 (−7.4) | −0.0397 (−10.4) |

**Negative in every cell of every regime.** Chasing momentum has negative
expectancy here whatever the volatility state.

## 5. What this actually establishes

Same data, same method, two targets:

| target over the next 12 bars | predictor | rank IC | t |
| --- | --- | --- | --- |
| **realised volatility** | trailing volatility | **+0.666** | +665 |
| signed return | trailing return (momentum) | −0.023 | −11.2 |

**Magnitude is enormously predictable. Direction is barely predictable, and what
little there is points against momentum.**

That asymmetry is the whole answer, and it resolves the paradox in the premise.
Sustained runs are common *because volatility clusters and tails are fat* — the
market tells you reliably **when** something will happen and almost nothing about
**which way**. Runs are a magnitude phenomenon being read as a direction
phenomenon.

## 6. Where this points

The negative result is specific: *directional* intraday momentum on this
universe. It says nothing against:

* **Volatility as the traded object.** An IC of +0.67 is not a marginal edge. It
  is not harvestable by buying and selling stock — that requires direction — but
  it is exactly what options, variance and dispersion trades are for, and it is
  the strongest signal found anywhere in this project.
* **Volatility for sizing and risk.** Nothing in the current design uses a
  forecast this good. Position size, stop distance and the no-trade decision all
  currently use a trailing estimate where a forecast would do.
* **Momentum at horizons this data cannot see.** Everything here is intraday.
  Cross-sectional momentum is a documented multi-month effect; nothing measured
  above bears on it, because 5-minute IEX bars over 15 months cannot.

The methods used are in the repository and are signal-agnostic: shuffle tests
against a matched null, IC by implementation lag, non-overlapping significance,
AUC alongside t-statistics so effect size is visible next to significance.
