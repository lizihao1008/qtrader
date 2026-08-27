# R03 — A trend follower that actually holds trends

**Date:** 2026-08-26
**Strategy:** `trend_ratchet` (redesigned), universe `us_liquid_22`, 1-minute IEX bars
**Window:** `mine` 2026-02-02 → 2026-06-01 (82 sessions). `validate` remains spent.

## 1. The complaint, and it was right

Two observations from a live session made the same point. USAR fell steadily
for three hours after the strategy's exit and it never shorted. BHVN rose 24%
after a short was covered and it never went long. A trend follower that watches
trends go past is not doing its job.

Three separate faults turned out to be responsible, and only the third was the
one I first suspected.

## 2. Fault one — a fixed span cannot see a slow move

Peak `|z|` reached during BHVN's session, by measurement span:

| span | 15 | 30 | 60 | 120 | 240 | session-to-date |
| --- | --- | --- | --- | --- | --- | --- |
| BHVN, after the 09:39 cover | 1.71 | — | **1.78** | 2.35 | 2.67 | **3.19** |

BHVN rose 24% over five hours. Spread across that many bars the *per-bar* drift
is small, so over any single hour it is under 2 sigma — the 60-bar estimator
read noise all day. The session as a whole was a 2.27-sigma event; only a
statistic whose window grows with the day can see that.

Hence `session_drift_zscore`: `z = sum(r since the open) / (sigma * sqrt(n))`.
Under a driftless random walk the numerator is `N(0, n·sigma²)`, so this is
exactly standard normal with no window, no span, and no parameter — verified by
simulation at bars 1, 5, 30, 120 and 389. It is now the default estimator.

## 3. Fault two — an event trigger cannot catch a state

The old entry required a MACD crossing *and* a significant trend on the same
bar. Measured on the two sessions in question:

| | bars with \|z\| ≥ 2.5 | crossings | crossings with \|z\| ≥ 2.5 |
| --- | --- | --- | --- |
| USAR after 09:45 | 1 / 204 | 17 | **0** |
| BHVN after 09:39 | 4 / 210 | 13 | **0** |

A slow trend is significant for long stretches while the oscillator crosses only
at its edges, so the conjunction almost never fires. The entry is now a
**state**, asked on every bar: significant drift, oscillator on the same side,
optionally still accelerating. The crossing is retained only as an *exit*
option, where an event is the right object.

Hysteresis keeps that honest: after an exit a symbol is disarmed until `|z|` has
cooled below `trend_z_reset`. Without it, a stop taken inside a live trend is
followed by an immediate re-entry.

## 4. Fault three — the exit was closing everything after ten bars

With the state entry in place, holding periods were still 10 bars. Instrumenting
the exits found the cause immediately: **81% of positions were being closed by
the opposite MACD crossing.** A sustained trend flips the histogram constantly;
exiting on each flip defeats the ratchet entirely.

That exit is now off by default and replaced by one matched to a state entry:
the position is released when the *drift statistic* reaches `trend_z_reset` on
the other side — the case for the trade has lapsed, not merely paused. The same
band therefore governs both getting out and being allowed back in.

## 5. What changed

Trade anatomy on `mine`, before and after:

| | old (event entry) | new (state entry) |
| --- | --- | --- |
| winners | +26.9 bps, held 26 bars | **+85.5 bps, held 173 bars** |
| losers | −19.0 bps, held 13 bars | −48.6 bps, held 46 bars |
| payoff ratio | 1.42 | **1.76** |
| breakeven hit rate | 41.3% | **36.3%** |
| actual hit rate | 39.3% (−2.0 pts short) | **36.6% (+0.3 pts over)** |
| gross per trade | −0.90 bps | **+0.45 bps** |
| median hold | — | 43 bars (p90 289, max 379) |
| trades per symbol per day | 0.15 | 0.62 |

Winners now run nearly seven times longer than losers and earn 3.2x more than
before. The strategy **clears its own breakeven hit rate on gross**, where the
previous version missed it by two points.

On today's session it behaves as asked: BHVN is bought at 12:22 at 16.33 and
held (16.72 at the time of writing), while USAR is correctly left alone.

## 6. What did not change

`+0.45 bps` per trade carries `t = +0.17`. It is not distinguishable from zero,
and costs are 3.0 bps per round trip, so the net result on `mine` is −4.16%.

The structure is much better; the edge is still absent. Those are separate
claims and only the first is supported. What has genuinely improved is that the
strategy now fails for one reason instead of three — the entry signal — rather
than throwing away whatever edge it had through a mis-specified exit.

## 7. Why USAR was right to be skipped

Worth stating plainly, because it looks like a miss and is not. Measured against
its own volatility:

| | day move | per-bar sigma | in sigma·sqrt(n) | a random walk does this or more |
| --- | --- | --- | --- | --- |
| USAR | −3.09% | 28.6 bps | **−0.73** | 47% of the time |
| PLTR | +3.20% | 14.2 bps | +1.52 | 13% |
| BHVN | +18.54% | 55.3 bps | **+2.27** | 2% |

USAR's three-hour decline is what a 28.6 bps/bar random walk does routinely. The
eye reads sustained direction as significance; the statistic does not, and no
honest version of it will. Building one that calls USAR a trend means building
one that calls half of all noise a trend too.

## 8. Follow-up — why the entry is late, and whether that can be fixed

BHVN was bought at 12:22 at 16.33, having bottomed at 13.38. Two questions
followed: why so late, and why at that particular crossing.

**Why 12:22.** The binding condition was the trend gate, not momentum. Every
golden cross before it was blocked by the same thing:

| cross | price | session z | histogram | cross_z | verdict |
| --- | --- | --- | --- | --- | --- |
| 10:42 | 14.76 | +1.09 | +0.0113 | **+2.28** | z below 2.5 |
| 11:16 | 15.21 | +1.24 | +0.0016 | +0.40 | z below 2.5 |
| 11:34 | 15.34 | +1.34 | +0.0026 | +1.03 | z below 2.5 |
| **12:21** | **16.29** | **+2.64** | +0.0009 | +1.44 | **enter** |

This is the session estimator's structural cost, and it is not a bug: cumulative
evidence reaches significance only once most of the move has already happened.
Note also that the entry cross is genuinely *weaker* than 10:42's (cross_z +1.44
against +2.28), because nothing in the rule prefers a strong crossing to a weak
one — only that the histogram be on the right side. The bar itself was not
"declining momentum" as it appeared: the histogram had just turned up with the
largest one-bar gain in ten bars. But the MACD *level* was well below its 11:00
peak, which is what the chart shows.

**Fix one: demand a decisive crossing.** Tested across the state design:

| min_cross_z | 0.0 | 0.5 | 1.0 | 1.5 | 2.0 | 2.5 |
| --- | --- | --- | --- | --- | --- | --- |
| gross/trade | +0.45 | −0.03 | −0.07 | −1.61 | +0.25 | −0.64 |

No gradient, nothing better than off. It does not help.

**Fix two: two timescales.** Trigger on a fast estimator, confirm with the
session's drift — "it is moving now" and "today is a trending day, this way".
Implemented as `session_confirm_z`. On BHVN today it enters at **10:09 at
14.53**, capturing +15.1% of what followed against the current rule's +3.5%.

Across 82 sessions it changes nothing:

| trigger | confirm | trades | gross/trade | t |
| --- | --- | --- | --- | --- |
| session 2.5 | — | 1115 | **+0.45** | +0.17 |
| ewma/30 2.5 | 0.0 | 1380 | +0.17 | +0.08 |
| ewma/30 2.5 | 0.5 | 1334 | −0.22 | −0.09 |
| ewma/30 2.5 | 1.0 | 1247 | +0.12 | +0.05 |

**Entering earlier on BHVN is one favourable anecdote.** Over ~1,300 trades the
earlier entry captures no more edge, which is what has to happen when the edge
is zero: being in the noise sooner is not being in a trend sooner. The default
is unchanged; `session_confirm_z` ships off and documented.

A caution worth recording: the strategy now carries 21 parameters, each added to
answer a specific question and each measured. That is a lot of surface for a
rule with no established edge, and it should be pruned before anything is built
on top of it.

## 9. Next

* This is all on `mine` and none of it is confirmed. `validate` was spent in
  R02, so confirming the redesign needs the wider dataset in PROGRESS.
* The strategy is at gross breakeven with a 3 bps cost floor. Both the negative
  short-horizon IC (R02 §5) and this point to the same experiment: **run the
  same design on 5- or 15-minute bars**, where the same edge per trade is spread
  over a quarter as many round trips. Only the config's `timeframe` changes.
* `min_cross_zscore` remains available and unevidenced.
