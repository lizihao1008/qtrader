# R18 — Displacement, and why a book cannot rank with a statistic that does not

**Date:** 2026-09-02
**Strategy:** `sr_momentum`, 5-minute, `m5_mine`
**Instruction:** let a clearly stronger new candidate replace a clearly weaker
holding — one already at a loss — keeping the book size fixed, minimising trade
count, and entering only on high confidence.

**Result: implemented in full, and both halves make the strategy worse.**
Raising the confidence bar degrades gross monotonically; displacement degrades
it at every bar tested. The cause is the same for both, and it is not the
mechanism: `|momentum_z|`, the statistic the book ranks and displaces by, has
**rank IC +0.0001** against trade outcomes.

## 1. What was built

`_respect_book_limit` became `_resolve_book`, returning `(opening, displaced)`.

* Free slots are filled strongest-first, unchanged.
* Beyond that, a queued candidate may evict a holding only when the holding is
  **currently losing**, is the **weakest** replaceable one, and the candidate
  beats it by at least `displace_margin`.
* **The book never grows**: one holding leaves per candidate admitted this way.
  Winners are never displaced.

Eight tests, including a property test over 200 random book states asserting the
cap is never breached and nothing opens on top of a holding. That test caught a
real gap in the first draft.

**The margin has to be large.** A displacement pays two round trips — the
incumbent's exit and the replacement's entry — so at 3 bps each the candidate
must be worth more than **6 bps** of edge over the position it evicts.

## 2. Raising the confidence bar

| `momentum_z_min` | trades | gross/trade | t | total return | turnover |
| ---: | ---: | ---: | ---: | ---: | ---: |
| **0.25** | 4,236 | **+1.80 bps** | +1.37 | **−7.38%** | 3.84x |
| 1.00 | 4,037 | +0.64 | +0.51 | −12.80% | 3.66x |
| 1.50 | 3,542 | **−1.28** | −1.04 | −19.42% | 3.21x |
| 2.00 | 2,607 | −1.11 | −0.90 | −14.18% | 2.37x |

"Enter only when confidence is very high" is exactly backwards here. The
strongest momentum readings are the *worst* trades, and gross turns negative
above 1.0. Trade count does fall as asked — 4,236 to 2,607 — but each remaining
trade is worse, so the reduction costs money rather than saving it.

## 3. Displacement

| entry bar | no displacement | margin 1.0 | margin 1.5 |
| ---: | ---: | ---: | ---: |
| z = 0.25 | **+1.80 bps** | +0.19 | +1.04 |
| z = 1.00 | **+0.64** | −0.37 | +0.29 |
| z = 1.50 | −1.28 | −1.47 | **−1.09** |
| z = 2.00 | −1.11 | −1.11 | **−0.96** |

Worse or equal in seven of eight cells, and it raises trade count and turnover
in every one — the opposite of the "fewer trades" requirement, and unavoidable,
since each displacement adds an exit and an entry by construction.

## 4. Why: the ranking statistic does not rank

`_resolve_book` sorts candidates and eviction targets by `|momentum_z|`.
Measured against what the trades actually did, over 4,236 round trips:

    rank IC  +0.0001   (t = +0.00)

| `|momentum_z|` at entry | trades | mean | gross | hit rate |
| --- | ---: | ---: | ---: | ---: |
| weakest | 848 | 0.50 | **+4.33 bps** | 39.4% |
| 2 | 847 | 0.89 | **+6.49** | 36.7% |
| 3 | 847 | 1.24 | +1.74 | 33.6% |
| 4 | 847 | 1.60 | **−3.91** | 33.4% |
| strongest | 847 | 2.52 | +0.34 | 35.3% |

Zero monotone information, and the hit rate *falls* as conviction rises. The
book has been ranking with a coin, and displacement makes it act on that ranking
more often — which is why it hurts rather than merely costing turnover.

This also settles §2: a higher entry bar is the same operation applied to
admission instead of ordering, so it fails for the same reason.

## 5. What this means for R17

R17 found that the trades the book admits are no better than the ones it rejects
(−0.46 against +0.71 bps, t = −1.02) and identified the mechanism: with the book
full 83% of the time, `_resolve_book` almost never runs and admission is decided
by exit timing.

Fixing the mechanism does not help, because the ranking it enables is
uninformative. The queue was not the binding problem; it was **masking** the
fact that the strategy has no way to tell its good candidates from its bad ones.
Randomised admission and ranked admission perform the same because there is
nothing to rank by.

## 6. Status

`displace_margin` is implemented, tested and **off by default**. It is correct
code for a mechanism that cannot pay for itself here, and it would become useful
the moment a candidate score with real IC existed — which is the actual missing
piece, and has been since R04.

`momentum_z_min` stays at the R16 value of 0.25: the lowest bar tested is the
best, which is another way of saying the filter is not selecting.

Nothing changes the standing conclusion. Best gross anywhere in this grid is
+1.80 bps against a 3.00 bps round trip, and R10 §5 shows even that 3.00
understates the cost where these trades happen.
