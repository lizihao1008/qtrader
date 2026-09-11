# R17 — The book limit, not the signal, decides which trades happen

**Date:** 2026-09-02
**Strategy:** `sr_momentum`, 5-minute, `m5_test` (observation) and `m5_mine` (measurement)
**Question:** on the pictured NVDA session there were several swings before the
14:50 short. Why did the strategy decide so late?

**Answer: it did not decide late. It decided six times and could not act, because
the book was full every time.** The constraint that determines which trades
happen is `max_positions`, and it selects no better than chance — arguably
slightly worse.

## 1. The pictured session

NVDA, 2026-03-16. The strategy proposed an entry at **10:35, 10:40, 11:30,
14:10, 14:15 and 14:50**. Exactly one became a trade: the 14:50 short, filled
14:55, which lost −274.

At every one of those six bars the book already held **6 of 6** positions.

| decision bar | proposed | `|momentum_z|` | positions open |
| --- | ---: | ---: | ---: |
| 10:35 | SHORT | 0.83 | 6 |
| 10:40 | SHORT | 0.98 | 6 |
| 11:30 | LONG | 0.43 | 6 |
| 14:10 | LONG | 0.85 | 6 |
| 14:15 | LONG | **1.25** | 6 |
| **14:50** | SHORT | **0.39** | 6 → a slot freed |

The trade that happened had the **weakest** momentum reading of the six. It was
not chosen; it was the one whose proposal coincided with a slot opening.

The session range was 310 bps over 78 bars, and the "swings" visible in the
chart are roughly 108 bps peak to trough — small enough that the watched level
(round number 184.00) sits inside the noise, so breaks register and unregister
repeatedly.

## 2. This is the normal state, not an unlucky day

Across `m5_test`, during 10:00–16:00 ET:

* the book is at its 6-position cap on **82.9% of bars**; the median number of
  positions held is **6 of 6**;
* of **10,668** entry proposals, only **2,119 (19.9%)** ever became a trade;
* **92.4%** of proposals were made while the book was already full.

Four fifths of everything the strategy decides is discarded by capacity.

## 3. The book does not select well

If the cap were rationing scarce capital toward the best setups, the trades
taken would outperform the ones rejected. Measured on `m5_mine`, scoring every
proposal by its own realised forward return over the strategy's horizon:

| | proposals | forward return | hit rate |
| --- | ---: | ---: | ---: |
| rejected (book full) | 16,471 | **+0.71 bps** | 49.7% |
| taken | 4,240 | **−0.46 bps** | 49.2% |

Taken minus rejected: **−1.16 bps, t = −1.02**. The trades the book admits are
statistically indistinguishable from the ones it turns away, and the point
estimate is the wrong way round.

## 4. Why, in the code

`_respect_book_limit` ranks by `|momentum_z|` — but only among **new candidates
competing for already-free slots**:

    free = self.max_positions - int((position != FLAT).sum())
    if free <= 0:
        return np.zeros_like(opening)

When the book is full it returns nothing. A new candidate is never compared
against an existing holding, so a 1.25-sigma setup cannot displace a 0.2-sigma
position that happens to be open. Since the book is full 83% of the time, the
ranking almost never runs, and admission is decided by **exit timing** —
effectively a queue.

That is why the strategy appears to "enter late" on chart after chart: the early
proposals are real and are simply dropped, and the one that survives is whichever
coincided with an exit.

## 5. What this invalidates

**Every veto-filter experiment in this project is confounded by it.** R09, R10
§3 and R11 §2 each found that adding a filter *raised* the trade count, which I
attributed to freed slots. That mechanism is now quantified: a veto returns a
slot to a queue that is saturated 83% of the time and refills it immediately,
with a candidate no worse and no better. The filter's own information was never
what those tests were measuring.

It also explains the observations that prompted R10 §5c and R14: the MU, TSLA
and NVDA cases all looked like a lagging entry signal, and the momentum
estimator genuinely does lag — but the larger effect was that the earlier,
better proposals had nowhere to go.

## 6. What it does not settle

Relaxing the cap is not an obvious fix and has already measured badly once:
R11 §2 widened the book to 12 slots at half weight, on the same gross exposure,
and total return fell from +4.86% to −4.82%. That test admitted *more* weak
candidates rather than admitting *better* ones, which is a different change from
the one this note argues for.

The change this note points at is **displacement**: allow a materially stronger
candidate to replace a materially weaker holding, so the ranking runs when it
matters instead of almost never. That has not been implemented or tested, it
adds turnover on a strategy already paying 3.66x/day, and R10 §5 shows the cost
model understates what that turnover costs. It is a hypothesis, not a
recommendation.

Nothing here rescues the strategy: `m5_test` gross is +1.52 bps at t = +0.78
against a 3.00 bps round trip. The finding is about **why the measurements
behaved the way they did**, and it applies to any future strategy that shares
this book structure.
