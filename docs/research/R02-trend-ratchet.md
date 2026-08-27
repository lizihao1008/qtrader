# R02 — MACD-triggered trend entry with a ratcheting volatility stop

**Date:** 2026-08-26
**Strategy:** `trend_ratchet`, universe `us_liquid_22`, 1-minute IEX bars
**Windows:** `mine` 2026-02-02 → 2026-06-01 (82 sessions) · `validate` 2026-06-01 → 2026-07-28 (38 sessions)

## 1. What was built

Three decisions, each with a definition rather than a threshold on a chart.

**Trend.** Over a trailing window `W`, log price is regressed on bar index and
the slope standardised against its sampling distribution **under a driftless
random walk**:

    b = OLS drift per bar
    z = b / (sigma * scale(W)),   scale(W)^2 = sum_k (sum_{i>=k} w_i)^2,  w_i = (i - mean_i)/Sxx

The regression's own t-statistic is deliberately not used. Its standard error
assumes independent residuals; a random walk's residuals around a fitted line
are not, and simulation puts `|t| >= 2` at **~80%** on driftless walks. The
correct scale makes `z` standard normal under that null — verified by
simulation (20k paths: sd 1.013, P(|z| ≥ 1.96) = 5.3% against a nominal 5%).
`|z| >= trend_z_min` is the entry filter, and it now means what it says.

The window must fit inside one session; otherwise a 60-bar window straddling an
overnight gap reads the gap as drift.

**Timing.** MACD is a band-pass filter on price, and its histogram is that
filter's momentum. A sign change is a change in the sign of medium-frequency
acceleration — a timing event, used only to trigger an entry the trend test has
already authorised. Continuation (cross agrees with `sign(z)`) and reversal
(cross opposes it) are separately switchable, because they are different bets.

**Risk.** One sigma unit is `sigma_H = sigma_bar * sqrt(horizon_bars)` — the
same sigma that standardises the trend, so "a 2.5-sigma trend" and "a 1-sigma
stop" refer to one quantity. For a long, with `M_t` the highest close since
entry:

    stop_t = max( entry - s*sigma_H*P ,  M_t - r*sigma_H*P ),    r >= s

This single expression is the whole exit policy:

* it starts at `entry - s*sigma_H`, so an immediate adverse move costs one risk
  unit and no more;
* it never falls, because `M_t` never falls — profit past the ratchet cannot be
  given back;
* it only begins trailing once `M_t - entry > (r-s)*sigma_H`, i.e. once the
  trade is genuinely ahead.

Position size follows from the same geometry: `w = clip(risk_per_trade /
(s*sigma_H), 0, max_weight)`, so `w * stop_distance` is a constant fraction of
capital and volatile names get smaller positions. The weight is frozen at entry.

Barriers are evaluated on **closes** and filled at the next open, like every
other signal. Stopping out at the exact barrier price inside a bar would claim a
fill the simulator cannot produce; the cost of that honesty is that a gap
through the stop loses more than one risk unit, as it would in reality.

## 2. The risk machinery does exactly what it was asked to

From 821 round trips on `mine` at `trend_z_min = 2.0`:

| | n | mean gross | mean MFE | mean MAE | mean hold |
| --- | --- | --- | --- | --- | --- |
| winners | 323 | **+26.9 bps** | +45.2 | −8.0 | 26 bars |
| losers | 498 | **−19.0 bps** | +9.7 | −25.0 | 13 bars |

Losers are held half as long as winners and barely get to run against the
position (mean MAE −25 bps ≈ one risk unit). Winners are let run and are almost
never deeply underwater first (mean MAE −8 bps). That is precisely "cut the loss
immediately, let the profit run, take it when the retracement exceeds the
range", and the geometry delivers a **payoff ratio of 1.42**.

Which converts the whole question into one number:

    breakeven hit rate = 1 / (1 + 1.42) = 41.3%
    actual hit rate    = 39.3%

**The strategy fails by about two percentage points of hit rate, before costs.**
Costs then add 3.0 bps per round trip against a mean gross of −0.9.

## 3. The entry signal has no measurable edge

The episode screen flagged one candidate on `mine`: `abs_trend_zscore`, the only
monotone profile of the eighteen screened, t = +2.46 against a Bonferroni bar of
2.99. Stronger trend evidence, better outcome — plausible, and below the bar.

Per-trade gross edge by threshold:

| | z ≥ 2.0 | z ≥ 2.5 | z ≥ 3.0 |
| --- | --- | --- | --- |
| `mine` (n) | 821 | 245 | 82 |
| `mine` mean gross | −0.90 bps (t = −0.81) | +2.86 (t = +1.25) | +5.20 (t = +1.13) |
| `validate` (n) | 412 | 82 | 15 |
| `validate` mean gross | +1.36 bps (t = +0.82) | **−0.85** (t = −0.26) | +2.80 (t = +0.27) |

On `mine` the edge rises with the threshold. **On `validate` it does not** —
+1.36 → −0.85 → +2.80 has no gradient, and no threshold produces a positive
net-per-trade. No t-statistic on either window exceeds 1.3.

The monotone bin profile was noise, which is what a t of 2.46 against a bar of
2.99 is supposed to mean. The multiple-testing correction earned its keep.

`trend_z_min = 2.5` was pre-committed as the middle of the tested range before
`validate` was run, specifically so the shipped value could not become the
in-sample optimum (3.0–3.5 by both measures).

## 4. Headline results

| | `mine` (82 sessions) | `validate` (38 sessions) |
| --- | --- | --- |
| net return | +0.08% | −0.40% |
| gross PnL | +$1,036 | −$74 |
| costs | $959 | $330 |
| trades | 245 | 82 |
| hit rate | 41.2% | 37.8% |
| daily turnover | 0.78x | 0.56x |
| max drawdown | −0.87% | −0.66% |

Turnover is 0.6–0.8x per day against 7.3x for the cross-sectional strategy — the
ratchet is doing its job of holding positions. The result is flat rather than
badly negative, and that is the correct reading: an edge of zero, managed well,
produces approximately zero minus costs.

## 5. What this establishes

1. **The risk framework is sound and reusable.** The barrier is monotone, the
   payoff geometry is what it claims to be, and the breakeven hit rate is
   computable in advance from `stop_sigmas` and `trail_sigmas`. Any future
   entry signal can be dropped into it and judged against 41.3%.
2. **The entry signal is the missing piece, and the bar is precise.** A trend
   filter plus a MACD cross gives 39–40% at a 1.42 payoff. It needs ~2 points
   more hit rate to break even before costs, and ~4 more to cover them.
3. **The trend statistic is worth keeping regardless of this strategy.**
   `drift_zscore` is a correctly calibrated measure of trend strength; the naive
   version would have called 80% of random walks trending.
4. **Minute-bar trend continuation is not visible here.** The rank IC of `z`
   against forward returns is negative at every horizon (−0.007 to −0.005,
   t = −4.1 to −2.8): over 5–30 minutes these names revert rather than continue.
   That is consistent with R01 and it is an argument against 1-minute trend
   following on this universe, not against the machinery.

## 6. Next

* Test the same barrier on a **longer bar interval** (5-minute or 15-minute).
  Both the negative short-horizon IC and the cost floor point the same way, and
  the strategy code needs no change — only the config's `timeframe`.
* Longs beat shorts on both windows (`mine`: +0.8 vs −2.5 bps) over a period
  when the benchmark rose 9.5%. Before reading anything into that, shorts still
  have no borrow cost modelled.
* `validate` is now spent for this strategy.

---

## 7. Addendum — two statistics that need less history (2026-08-26)

Two follow-up questions: can the trend test avoid needing 60 bars of
same-session history, and can the *sharpness* of a MACD crossing be measured
rather than eyeballed? Both can, and both were built and calibrated. Neither
improved the strategy.

### 7.1 An estimator with no window

`ewma_drift_zscore` weights returns exponentially and lets its own standard
error account for how little it has seen. With `beta = 1 - 2/(span+1)` and `n`
returns since the session opened::

    z = (sum_k beta^k r_{t-k}) / (sigma * sqrt(S2)) ,   S2 = sum_{k<n} beta^(2k)

Both sums are closed-form, so `z` is standard normal under a driftless random
walk at **every** bar. Simulation over 3,000 paths confirms it: sd 1.00 and
P(|z| ≥ 1.96) ≈ 0.05 at bars 1, 2, 10, 60, 200 and 389 alike. Early in a session
`S2` is small, so the same raw move yields a smaller `z` — the statistic demands
more evidence exactly when it has less, which is what the hard 60-bar window was
crudely approximating.

This removes the blackout entirely. On `mine`:

| estimator | trades | gross/trade | t | net/trade | hit rate |
| --- | --- | --- | --- | --- | --- |
| `window` (60 bars) | 242 | +3.00 bps | +1.33 | +0.00 | 45.9% |
| `ewma` (span 60) | 1,104 | +1.01 bps | +0.59 | **−1.99** | 40.3% |

**The blackout was filtering out bad trades.** Removing it multiplies the book
by 4.5x and the extra trades are worse than the ones already being taken. The
statistic is better; the trading is not.

`window` therefore stays the default — on a cost argument, not a statistical
one. Both estimators are calibrated the same way and neither is wrong; with no
established edge, the one that trades a quarter as much is the right default.

### 7.2 The crossing angle, made scale-free

The chart intuition — a steep MACD crossing is decisive, a shallow one is not —
is real, but the literal angle is not measurable: it depends on the price level
and on how the chart was drawn.

What the eye judges is how fast the histogram moves through zero. `h` is a
linear filter of the price level, so `dh` is a linear filter of returns; feeding
a unit return shock through it gives an impulse response `g`, and under the null
`Var(dh) = (sigma_bar * P)^2 * ||g||^2`. Hence

    cross_z = dh_t / (sigma_bar * P * ||g||)

is standard normal — `|cross_z| >= 2` means the histogram is opening faster than
95% of what noise produces. Because the MACD is restarted each session (so no
overnight gap leaks into it), the filter is not converged early and `||g||` is
computed per within-session position: 0 at the open, 0.0638 at bar 1, converging
to 0.0867 by bar ~20. Simulation confirms sd ≈ 1.00 from the session's second
bar onward.

As a gate on `mine`:

| gate | trades | gross/trade | t | net/trade | hit rate |
| --- | --- | --- | --- | --- | --- |
| off | 242 | +3.00 bps | +1.33 | +0.00 | 45.9% |
| \|cross_z\| ≥ 1 | 166 | +2.80 | +0.98 | −0.20 | 47.6% |
| \|cross_z\| ≥ 2 | 65 | +5.85 | **+1.10** | +2.85 | 50.8% |

The direction is right — sharper crossings do win more often (45.9% → 50.8%) and
earn more per trade. But the **t-statistic does not improve** (1.33 → 1.10): the
gate is discarding trades faster than it is improving them, which is what
selecting on noise looks like. It ships off by default.

### 7.3 What this adds up to

Neither result contradicts §5: the risk machinery is sound and the entry signal
has no measurable edge. Two better-behaved statistics do not manufacture one.
What they do provide is a cleaner instrument for the *next* signal — a trend
measure with no dead zone at the open, and a decisiveness measure for any
oscillator crossing — both calibrated the same way, so a threshold means the
same thing across them.

`validate` was already spent on this strategy, so none of §7 has been confirmed
out of sample. Confirming it needs the wider dataset listed in PROGRESS.
