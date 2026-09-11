# R10 — Where these trades actually happen, and what they should have been charged

**Question asked:** the setup charts look like most decisions are made on the
opening gap. Is that true, and if so should the first decision wait ten minutes
and use today's opening bars instead of the previous day's?

**Answer: the observation is right, the diagnosis is not, and the remedy makes
the backtest worse. Following it up, though, surfaced something that matters
more than the entry timing — every result in this project has been charging
open-of-session trades a mid-day spread.**

---

## 1. The observation is correct, and stronger than it looked

Entries by bar of the session, `sr_momentum` baseline on `m5_mine`, 2,788 round
trips:

| decided on | trades | share | cumulative |
| --- | --- | --- | --- |
| bar 1 (09:35–09:40) | 1,370 | **49.1%** | 49.1% |
| bars 2–5 (to 10:00) | 515 | 18.5% | 67.6% |
| bars 6–9 (to 10:20) | 103 | 3.7% | 71.3% |
| bar 10+ | 800 | 28.7% | 100% |

Half of every trade the strategy takes is decided in the second five-minute bar
of the day. Bar 0 has none, because the drift z-score needs one return to exist.

## 2. But the previous-day gap is not the mechanism

Attributing each entry to the level family that triggered it, matched across the
retest window (round levels are recomputed every bar, so a level broken three
bars ago no longer equals the current bracket — matching only at the entry bar
mislabels 42% of trades as "none"):

| level family | trades | share | mean gross |
| --- | --- | --- | --- |
| **round numbers** | 2,128 | **76.3%** | +2.71 bps |
| previous day | 399 | 14.3% | +9.72 bps |
| opening range | 242 | 8.7% | +0.10 bps |

Three quarters of the entries come from round numbers, not from yesterday's
high/low. At bar 0–1 specifically it is 1,130 round against 220 previous-day.

The mechanism is not "the gap jumps yesterday's level". It is that **$1 round
levels are never far away**, and the opening bars are the most volatile of the
day — realised 5-minute volatility is 41 bps at 09:35 against 12 bps at 15:20.
A level that sits within a fraction of one bar's range gets broken and retested
constantly. The open is where the setups are, because the open is where the
movement is.

## 3. The proposed remedy makes it worse

`no_entry_before` was implemented as a clock-based session-open blackout,
symmetric with the existing `no_entry_after`.

> Historical semantics: this section measures the original pure gate, which
> delayed live setups. R13 later replaced it with a setup-invalidating gate and
> re-ran the 09:40 experiment. That stricter version is also negative.

| variant | trades | gross/trade | t | net | total return | turnover |
| --- | --- | --- | --- | --- | --- | --- |
| **baseline** | 2,788 | **+3.50 bps** | +1.51 | +0.50 | −3.02% | 1.80x |
| no entry before 09:40 | 2,948 | +0.40 | +0.19 | −2.60 | −6.37% | 2.08x |
| no entry before 10:20 | 2,892 | −0.27 | −0.16 | −3.27 | −11.28% | 2.28x |

Two things to note.

**The trade count goes up, not down.** A blackout delays a live setup rather
than cancelling it: price is still beyond the level, the break re-registers
every bar, and the entry lands on the first admitted bar. Freed position slots
then admit names that the book was previously too full to take. Turnover rises
from 1.80x to 2.28x per day. This is asserted in a unit test, because it changes
how the parameter's backtest should be read.

**The open bucket was the only profitable one.** Mean gross by bucket: +8.32 bps
at bar 0–1, −3.29 at bars 2–5, +0.07 after 10:00. Removing the open removes the
only positive gross the strategy had.

## 4. The second half of the idea does help — at the flat cost model

Dropping previous-day levels and keeping today's own (`use_previous_day=false`):

| | trades | gross/trade | t | net | total return | Sharpe | maxDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 2,788 | +3.50 | +1.51 | +0.50 | −3.02% | −0.47 | −8.95% |
| **no previous-day levels** | 2,767 | **+4.62** | **+1.96** | +1.62 | **+0.30%** | +0.07 | −7.33% |

This is the first positive total return in the project. It should not be
believed, for two independent reasons.

**It is trial 18 against `m5_mine`.** The Bonferroni threshold for 18 trials at
α = 0.05 is |t| ≈ 3.0. A t of +1.96 is what the eighteenth variant of anything
looks like. The ledger records the count precisely so this cannot be forgotten.

**The gain is entirely in the bucket the cost model undercharges.** 50% of its
trades are decided at bar 0–1, earning +8.93 bps gross there against −0.42 to
−2.14 in the middle of the day.

---

## 5. The finding that matters: the cost model is flat and the spread is not

`config/backtest/*.yaml` charges 1.0 bps half-spread + 0.5 bps slippage, the
same at 09:35 as at 15:20. Roll (1984) effective half-spreads estimated on
1-minute bars for this universe:

| window | half-spread | vs mid-day |
| --- | --- | --- |
| 09:31–09:35 | **4.91 bps** | **14.4x** |
| 09:35–09:50 | no estimate — serial covariance is positive | — |
| 09:50–10:00 | 1.04 bps | 3.0x |
| 10:00–10:30 | 1.33 bps | 3.9x |
| 10:30–11:30 | 0.75 bps | 2.2x |
| 11:30–14:30 | 0.34 bps | 1.0x |
| 14:30–15:59 | 0.42 bps | 1.2x |

The flat 1.0 bps is roughly **3x conservative** in the middle of the day and
roughly **5x optimistic** in the first five minutes. Half of this strategy's
trades are in the second group.

The assumption-free version of the test: what would the half-spread have to be
for each bucket to break even, given its gross, at the config's 0.5 bps
slippage? `breakeven h = gross/2 − 0.5`.

**Baseline:**

| bucket | n | share | gross | breakeven h | measured h | |
| --- | --- | --- | --- | --- | --- | --- |
| bar 0–1 | 1,370 | 49% | +8.32 | 3.66 | 4.91 | **fail** |
| bars 2–5 | 515 | 18% | −3.29 | −2.14 | 1.04 | fail |
| bars 6–11 | 142 | 5% | +2.30 | 0.65 | 1.33 | fail |
| bars 12–23 | 181 | 6% | −0.56 | −0.78 | 0.75 | fail |
| bar 24+ | 580 | 21% | −0.27 | −0.64 | 0.36 | fail |

Expectancy at the flat 3.00 bps: **+0.50 bps/trade**. At the measured spreads:
**−3.09 bps/trade**.

**No previous-day levels** — the variant that looked positive:

| bucket | n | share | gross | breakeven h | measured h | |
| --- | --- | --- | --- | --- | --- | --- |
| bar 0–1 | 1,387 | 50% | +8.93 | 3.96 | 4.91 | **fail** |
| bars 2–5 | 513 | 19% | −0.42 | −0.71 | 1.04 | fail |
| bars 6–11 | 129 | 5% | −0.83 | −0.91 | 1.33 | fail |
| bars 12–23 | 182 | 7% | −2.14 | −1.57 | 0.75 | fail |
| bar 24+ | 556 | 20% | +2.00 | 0.50 | 0.36 | pass |

Expectancy at the flat 3.00 bps: **+1.62 bps/trade**. At the measured spreads:
**−2.06 bps/trade**.

One bucket passes, by 0.14 bps, on 556 trades, out of ten buckets inspected.
That is a slice, not a finding.

## 5b. Why the result is so poor — and why delaying makes it worse

Three candidate explanations, tested in order.

**Is the exit giving the move back?** No. Holding the same 2,788 entries with a
fixed horizon instead of the ratchet:

| exit rule | gross/trade | t |
| --- | --- | --- |
| **ratchet (stop 1.0σ / trail 1.5σ)** | **+3.50 bps** | +1.51 |
| fixed 3 bars | −0.18 | −0.17 |
| fixed 6 bars | +1.12 | +0.84 |
| fixed 12 bars | +0.94 | +0.56 |
| fixed 24 bars | +1.29 | +0.60 |
| fixed 48 bars | +1.78 | +0.65 |
| fixed 78 bars (to the close) | −6.57 | −0.71 |

The ratchet beats every fixed horizon. It is the best-working component in the
system, not the fault. Winners capture **64% of their maximum favourable
excursion** — the exit is releasing them near their best point.

**Is the entry direction wrong?** Yes, and this is the whole story. Among the
1,526 losing trades, **66% never saw their favourable excursion reach even half
of their adverse excursion** — mean MFE +38 bps against mean MAE −98 bps. These
are not trades that were managed badly. They were wrong from the first bar.

**How thin is the margin?** At zero cost:

| | win rate | breakeven win rate | margin | payoff ratio |
| --- | --- | --- | --- | --- |
| baseline | 45.3% | 43.3% | **+2.0 pts** | 1.31 |
| no entry before 10:20 | 40.7% | 40.9% | **−0.2 pts** | 1.44 |

The entire edge is two percentage points of win rate before any cost. The
standard error of a 45.3% rate on 2,788 trades is 0.94 points, so +2.0 is about
2σ — the same borderline reading as the t = +1.51 on gross, arrived at
independently.

**Why the delay is so destructive** is now visible, and it is not what the
turnover numbers suggested. The payoff ratio actually *improves*, 1.31 → 1.44,
so the surviving trades are not smaller in a damaging way. What collapses is the
win rate, 45.3% → 40.7%, straight through its own breakeven.

The reason is selection, not timing. **A break-and-retest that is still alive at
bar 10+ is one where price failed to run.** Waiting does not sample the same
setups later; it samples the subset that already refused to work, and it
discards the ones that resolved immediately. The delay is a filter that keeps
the failures.

---

## 5c. Reading one loser: the momentum filter measures position, not direction

A charted trade — TSLA 2025-01-30, short at 10:25 ET, −324 bps — prompted the
question of how the rule could short into what was visibly a reversal. It could,
and the reason is a genuine defect in the momentum filter.

State at the decision bar:

| | |
| --- | --- |
| session opened | 403.80 |
| session low | 386.08, set at **10:00** |
| price now (10:25) | 392.49 |
| drift since the open | **−284 bps** → `momentum_z` **−1.09** |
| last 3 bars | **+53 bps** |
| last 6 bars | **+120 bps** |
| last 12 bars | **+111 bps** |

Price had been rising for twenty-five minutes and the session low was
twenty-five minutes old. The filter still said "short", because
`session_drift_zscore` is `sum(r since the open) / (sigma * sqrt(n))` — every
return since 09:30 weighted equally. **A large early move fixes its sign for the
rest of the session regardless of what price does afterwards.** It is a
statement about where price sits relative to the open, not about where it is
going.

`momentum_estimator` was added, selecting `session` (as before) or `ewma`, the
recency-weighted estimator already in `features/trend.py`. On this trade it
reads −0.23 instead of −1.09 and the entry does not fire.

**Fixing it makes the backtest worse:**

| momentum estimator | trades | gross/trade | t | total return |
| --- | --- | --- | --- | --- |
| **session (stale)** | 2,788 | **+3.50 bps** | +1.51 | −3.02% |
| ewma, span 12 | 2,796 | +2.98 | +1.27 | −3.89% |
| ewma, span 6 | 2,803 | +1.13 | +0.49 | −8.59% |

The reason is visible in a direct slice: **19% of baseline entries are taken
against the last six bars' direction, and those earn +6.58 bps gross against
+2.77 for the ones that agree.** The stale indicator was accidentally producing
counter-trend entries, and on this universe fading beats following — which is
what R04/R05 measured directly (intraday momentum rank IC −0.023, t = −11.2).

So the criticism of the indicator is correct on mechanics and wrong on
consequence. It is broken, and it was broken in the profitable direction.

## 5d. Would requiring the forecast to agree have helped?

Asked of the same panel, where Kronos pointed opposite to the trade. Over **all
2,750 baseline trades carrying a score**, not the 40 charted extremes:

| | trades | share | hit rate | gross/trade |
| --- | --- | --- | --- | --- |
| Kronos disagreed | 1,837 | 67% | 45.2% | +3.58 bps |
| Kronos agreed | 913 | 33% | 45.8% | +3.86 bps |

Agreed minus disagreed: **+0.28 bps, t = +0.06.** Requiring agreement discards
two thirds of the book for a difference indistinguishable from zero.

Note this is *less* damning than the backtest in R09, which showed gross falling
from +3.50 to +1.13 when the filter was enabled. The two are consistent: the
filter's **direct** effect on the trades it keeps is ~zero, but vetoing entries
frees position slots, and the replacements the book then admits are worse. Most
of R09's damage was that indirect effect, not the filter's own selection.

## 5e. The structural objection

The same panel prompted a second reading: the pullback held above the session
low, so the structure was a higher low and the naive trade was long, not short.
Testing that condition across every trade with at least six bars of session
history (n = 903 — the requirement excludes most of the bar 0–1 book):

| | trades | hit rate | gross/trade |
| --- | --- | --- | --- |
| with the session structure | 669 | 41.4% | +0.57 bps |
| **against** the session structure | 234 | 37.6% | −1.35 bps |

Difference −1.91 bps at **t = −0.37**. By side it is sharper — shorts taken
against structure earn −4.63 bps against +5.31 with it — but on 121 trades, and
the long side flips the sign.

The intuition points the right way on the short leg. It is not distinguishable
from noise overall, and it contradicts the "fights the last six bars" slice
above, which says the opposite on a larger sample. Two weak, conflicting slices
of the same book are not evidence for either.

---

## 5f. "Why enter so long after the trend appeared?"

Asked of the same TSLA panel. The answer is that the panel is from the
**delayed** variant, and the delay caused the lateness.

Same symbol, same session, the two configurations side by side:

| configuration | entry | price | level | momentum_z | outcome |
| --- | --- | --- | --- | --- | --- |
| **baseline** | **09:40** | 395.60 | 400.00 | −1.59 | short taken as price left 400, before the fall to 386 |
| no entry before 10:20 | 10:25 | 392.49 | 395.00 | −1.09 | short taken 25 min after the low, into the rally — **−324 bps** |

The baseline entered at 09:40, at the start of the move. The blackout blocked
that signal, and the first bar it was allowed to act on was 10:25 — by which
time the trend had reversed. This is R10 §3's selection argument in one chart:
**the delay does not take the same trade later, it takes the reversal.**

## 5g. The retest was not filtering anything — an implementation bug

Chasing the same question exposed a defect. Measuring how old a break was when
the entry fired: median **0 bars**, mean 0.1. Across all 365,943 symbol-bars
carrying a live break, **81.4% were registered on that very bar.**

The cause is in `_track_break`:

    fresh_up = np.isfinite(broke_up) & (position == FLAT)

This tests "price is beyond a level", not "price was not already beyond it". So
a break re-registered on every subsequent bar, resetting `broke_age` to 0 and
clearing `retested` — and the same call then set `retested` back to True if that
bar's range happened to straddle the zone.

**"Break → come back → retest → hold", the thesis of the deep-research report,
was in practice a single-bar test of "price is beyond the level and this bar
straddles it".** The retest window never had a chance to accumulate. Removing
the requirement entirely changed the trade count by 19 out of 2,788 — which is
what alerted me to it.

Fixed: a break now registers once and ages while price stays beyond the level.
Share of live breaks at age 0 falls from 81.4% to 42.3%, and the age
distribution spreads across the whole six-bar window. Two regression tests pin
it.

**With the state machine corrected, waiting for the retest is worse than not
waiting:**

| | trades | gross/trade | t | net | total return | maxDD |
| --- | --- | --- | --- | --- | --- | --- |
| retest required (report's thesis) | 2,793 | +3.70 | +1.61 | +0.70 | −3.08% | −8.35% |
| **enter on the break** | 2,750 | **+4.25** | **+1.84** | +1.25 | −2.17% | −6.22% |

So the intuition — act when the signal appears, do not wait for a retest — is
supported here. It is also still `t = +1.84` on a window now carrying **26
recorded trials**, where Bonferroni puts the bar near |t| = 3.2, and R10 §5's
cost finding applies to it unchanged. It is a better number, not a result.

The config default stays `require_retest: true`, because that is what the report
specifies and the config documents the report. The measurement is recorded here.

---

## 5h. Why the return stayed negative while expectancy was positive

The two numbers had been disagreeing for several sections — net expectancy
positive, total return negative — and the cause is the position sizing.

On the `require_retest=false` variant (2,750 trades, +1.25 bps/trade
equal-weighted, **−2.17%** total), splitting by position size:

| notional quintile | mean notional | mean return | win rate | total P&L |
| --- | --- | --- | --- | --- |
| smallest | $4,207 | **+6.67 bps** | 50.4% | +$1,144 |
| 2 | $7,067 | +10.51 | 50.2% | +$4,160 |
| 3 | $9,686 | −2.78 | 45.3% | −$1,343 |
| 4 | $13,027 | −1.87 | 40.7% | −$1,358 |
| largest | $13,806 | **−6.27** | **35.1%** | **−$4,776** |

**The strategy bets biggest on its worst trades.** Equal-weighted mean +1.25 bps;
notional-weighted **−0.83 bps**. That gap is the entire discrepancy.

This is not a bug — the sizing rule is working exactly as specified. `weight =
risk_per_trade / (stop_sigmas * sigma_H)` makes every trade risk the same
fraction of equity between entry and stop, which is the textbook rule. It
implies `corr(sigma, notional) = −0.897`: **low volatility, tight stop, large
position.**

The trouble is that on this universe the edge is *larger* in volatile names:

| volatility quintile | mean sigma_H | mean notional | mean return | win rate |
| --- | --- | --- | --- | --- |
| lowest | 27.5 bps | $13,659 | −1.44 bps | 38% |
| 2 | 62.4 | $13,154 | −6.82 | 38% |
| 3 | 93.9 | $9,678 | +1.36 | 46% |
| 4 | 127.6 | $7,088 | +5.70 | 49% |
| highest | 226.0 | $4,215 | **+7.45** | **51%** |

Fixed-fractional risk sizing is correct when the edge does not depend on
volatility. Here it does, so the rule is **anti-correlated with the edge by
construction** and allocates the most capital to the setups with the least
signal.

A `sizing` parameter was added (`risk` | `equal`). With the same entries, exits
and fills, and only the position size changed:

| | trades | gross/trade | t | net | **total return** | Sharpe | maxDD | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| risk sizing, retest required | 2,793 | +3.70 | +1.61 | +0.70 | −3.08% | −0.48 | −8.35% | 1.81x |
| risk sizing, break entry | 2,750 | +4.25 | +1.84 | +1.25 | −2.17% | −0.34 | −6.22% | 1.75x |
| equal sizing, retest required | 2,793 | +3.70 | +1.61 | +0.70 | **+2.33%** | +0.23 | −9.66% | 2.53x |
| **equal sizing, break entry** | 2,750 | +4.27 | +1.85 | +1.27 | **+4.56%** | **+0.41** | −8.00% | 2.49x |

**And the win rate was never the problem.** It is 45.5% against a breakeven of
43.0% — above it throughout, carried by a payoff ratio above 1. A sub-50% win
rate is not a defect when winners are larger than losers.

### It still does not survive honest costs

The equal-sizing variant puts **58%** of its trades in the first two bars of the
session, up from 49%, because equal weighting stops shrinking positions in
exactly the volatile names the open produces. Applying the measured spreads:

| bucket | n | share | gross | breakeven h | measured h | |
| --- | --- | --- | --- | --- | --- | --- |
| bar 0–1 | 1,585 | 58% | +5.51 | 2.25 | 4.91 | **fail** |
| bars 2–5 | 312 | 11% | +3.36 | 1.18 | 1.04 | pass |
| bars 6–11 | 127 | 5% | +2.76 | 0.88 | 1.33 | fail |
| bars 12–23 | 179 | 7% | −1.83 | −1.41 | 0.75 | fail |
| bar 24+ | 547 | 20% | +3.56 | 1.28 | 0.36 | pass |

Expectancy at the flat 3.00 bps: **+1.27 bps/trade (+4.56% total)**. At measured
spreads: **−2.99 bps/trade.**

The sizing finding is real and mechanistic — it is a structural defect, not a
tuned parameter, and it would matter for any strategy in this repository whose
edge scales with volatility. The +4.56% is not. It is trial 28 on `m5_mine`
(Bonferroni |t| ≈ 3.2 against an observed +1.85), and it *increases* exposure to
the bucket the cost model gets most wrong.

---

## 5i. Why a big winner can finish flat: the give-back is sized at entry

Traced from one trade — MU short, 2026-06-29, decided 09:35, filled 09:40 at
1090.86.

| | |
| --- | --- |
| peak profit (on closes, 10:10) | **+543 bps** |
| trail give-back allowance | `1.5 x sigma_H` = `1.5 x 280` = **420 bps** |
| that allowance as a share of the peak | **77%** |
| exit signalled | 11:25, after surrendering 457 bps |
| realised | **+86 bps** gross, +78 net |

The strategy already implements a give-back rule — the ratchet *is* one. The
problem is what the allowance is measured against. `trail_sigmas * sigma_H` is
fixed at entry from the volatility at entry, and this entry was at 09:35, where
`sigma_H` read 280 bps. A trade can then be several hundred bps ahead and still
surrender almost all of it before the trail is touched.

This is the R10 §5 problem again in a different guise: the opening bars are
where the volatility estimate is largest, so both the position size *and* the
exit geometry are calibrated off the least representative reading of the day.

### The fix, and why it is not enabled

`max_giveback` caps the surrendered profit as a fraction of the peak, armed only
once the position is up more than it risked — scale-free, and needing no
threshold of its own. On the MU trade it does exactly what it should:

| | exit | realised |
| --- | --- | --- |
| ratchet only | 11:30 @ 1082.20 | +$697 |
| give back <= 50% | 11:00 @ 1062.96 | **+$2,163** |
| give back <= 35% | 10:30 @ 1055.00 | **+$2,856** |

On `m5_mine`, across 2,761 trades, it makes things worse:

| | trades | hit rate | gross/trade | t | total return | turnover |
| --- | --- | --- | --- | --- | --- | --- |
| **ratchet only** | 2,761 | 44.5% | **+4.33** | +1.86 | **+4.86%** | 2.50x |
| give back <= 50% | 3,200 | 46.0% | +2.25 | +1.13 | −3.78% | 2.90x |
| give back <= 35% | 3,373 | 47.5% | +3.68 | +1.94 | +2.69% | 3.05x |
| give back <= 25% | 3,591 | 47.1% | +2.64 | +1.49 | −2.37% | 3.25x |

The pattern is the textbook one and it is consistent across all three settings:
**the hit rate rises and the expectancy falls.** Cutting winners earlier turns
more trades into wins and makes every win smaller, and this strategy is carried
by its payoff ratio (1.31–1.44), not by its hit rate. Turnover rises too,
2.50 to 3.25x per day, because an earlier exit frees the symbol to re-enter — so
the fixed cost is paid more often as well.

The result is also **non-monotonic** in the threshold: 0.35 is better than both
0.50 and 0.25. A real relationship would not zig-zag; that is noise.

`max_giveback` is implemented, tested and **off by default**. It fixes the trade
it was built for and costs money across the sample, which is the whole reason a
single trade cannot decide an exit rule.

---

## 5j. Exits that follow the trend judgement, rather than a fixed barrier

The objection to §5i's cap: a hard take-profit is arbitrary, and the exit should
come from the same reading of the market that produced the entry. If the
strategy now thinks the trend is up, it should not still be short. That is a
consistency argument and it is correct as stated.

Three implementations, in increasing strictness about what counts as a changed
mind. All are off by default.

| parameter | closes the position when |
| --- | --- |
| `exit_on_reversal` | the trend statistic crosses to the other side by this much |
| `reversal_bars` | ...and has stayed there this many consecutive bars |
| `exit_on_opposite_signal` | the **full entry rule** would open the other way now |

The last one needs breaks to keep forming underneath an open position, which
normally does not happen — the gate is lifted only when that exit is enabled, so
the baseline state machine is untouched.

**All three need `momentum_estimator=ewma`.** The `session` estimator cannot
reverse within a day after a large early move (§5c), so it almost never fires
these: with `exit_on_opposite_signal` it changes the MU trade not at all.

### On the trade that prompted it

MU 2026-06-29, the short from 09:40. The EWMA statistic turns positive at
**10:50, with +278 bps still on the table**, while session drift stays negative
all day.

| | first short exits | realised | whole day |
| --- | --- | --- | --- |
| ratchet only (baseline) | 11:30 @ 1082.20 | +$697 | +$2 |
| give back <= 50% (§5i) | 11:00 @ 1062.96 | +$2,163 | +$1,487 |
| reversal exit @ 0.5 sigma | 11:05 @ 1072.01 | +$1,532 | +$3,694 |
| **opposite-signal exit** | 11:10 @ 1075.87 | +$1,260 | **+$5,367** |

### On the sample

| variant | trades | gross/trade | t | **total return** | mean hold | turnover |
| --- | --- | --- | --- | --- | --- | --- |
| **baseline (session, ratchet only)** | 2,761 | **+4.33** | +1.86 | **+4.86%** | 49.5 | 2.50x |
| ewma, no reversal exit | 2,817 | +3.49 | +1.51 | +1.68% | 48.5 | 2.55x |
| opposite-signal exit (session) | 2,875 | +3.91 | +1.75 | +3.32% | 47.5 | 2.60x |
| opposite-signal exit (ewma) | 3,900 | +1.46 | +0.89 | −8.61% | 34.6 | 3.53x |
| reversal @ 1.0 sigma | 5,712 | +1.04 | +0.95 | −14.95% | 22.5 | 5.16x |
| reversal @ 0.5, persist 4 bars | 4,946 | +0.87 | +0.68 | −14.24% | 27.4 | 4.48x |
| reversal @ 0.5 sigma | 7,755 | +0.21 | +0.26 | −26.73% | 15.7 | 6.99x |
| reversal @ 0.0 (plain sign flip) | 10,761 | +0.11 | +0.21 | **−35.65%** | 10.2 | 9.68x |

**The ordering is the finding.** Across eight configurations, the more
responsive the exit is to a change of trend, the better MU 2026-06-29 looks and
the worse the sample performs — monotonically, from +4.86% down to −35.65%.
Mean hold collapses from 49.5 bars to 10.2 and turnover quadruples, so the fixed
3 bps toll is paid four times as often on trades whose gross has fallen to zero.

### Why the consistency argument does not survive contact with the data

The premise is that a trend, once identified, either persists or reverses, and
that the statistic can tell which. R04/R05 measured that premise directly on
this universe and it is false: intraday momentum has **rank IC −0.023 at
t = −11.2**, reliably wrong-signed. A counter-move at 5-minute resolution is
usually noise that reverts, not a trend change.

So an exit built on "has the trend changed" is timing exits with a statistic
that has no forward information — and paying a round trip each time it is wrong.
The ratchet outperforms precisely because it makes no such claim: it is a pure
risk rule, giving back a fixed volatility-scaled amount and holding through
everything else.

MU 2026-06-29 is a real case where reacting was right. It is one of 2,761, and
the rule that would have caught it costs 13 to 40 points of return across the
rest.

**This does not close the question, it relocates it.** A trend-change exit needs
a judgement of persistence that is better than the momentum z-score — which is
what an LLM exit would have to supply. But the algorithmic result sets the bar:
any such judgement has to beat a barrier that makes no prediction at all, while
paying for the extra turnover it creates. That is the test to run before
believing an LLM exit, not after.

---

## 6. What this changes

**The user's instinct was right, and for a better reason than the one given.**
Decisions made in the opening minutes should be distrusted — not because a gap
distorts the levels, but because the backtest is paying mid-day prices for
opening-bell liquidity, and half the trades are there.

**It also means every earlier number in this project is inflated**, in
proportion to how much of its turnover sits in the first half hour. R09's
baseline of +3.50 bps gross at t = +1.51 was already indistinguishable from
zero; under measured spreads it is −3.09 bps/trade and not marginal at all. The
same correction applies to anything else in the repository with intraday
turnover concentrated at the open.

**Historical decision:** the original delay parameter was kept but left unset.
R13 supersedes its semantics and the active config: the first ten minutes are
now observation-only, pre-cutoff setups are consumed rather than delayed, and
the stricter rule was re-tested. It still raises turnover and materially worsens
the result, so the constraint is retained by instruction rather than as alpha.

## 7. Follow-up this creates

A **time-of-day-aware cost model** is now the highest-value change in the
repository, ahead of any signal work. It is a change to a production-critical
component (`backtest/costs.py`), it requires regression tests, and it would
re-score every result recorded so far — so it is not something to slip in as a
side effect of an entry-timing question. Recorded in `PROGRESS.md` as the next
action rather than done here.

Until it exists, the honest reading of any backtest in this project is: **valid
for trades after 10:30, optimistic before it, and badly optimistic in the first
ten minutes.**
