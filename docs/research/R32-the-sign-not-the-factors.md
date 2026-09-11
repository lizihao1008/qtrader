# R32 — Why the gross is negative: the sign, not the factor definitions

**Date:** 2026-09-09
**Question:** the gross P&L is negative — are the factors badly defined?

**Answer: no. The factors measure what they claim to, and they carry real
information. What is wrong is the directional assumption built on top of them:
on 1-minute IEX cross-sections, extreme relative momentum is followed by
reversion, and the more extreme the score the more reliably. Flipping the sign
turns gross positive in both windows tested — and it is still four times too
small to pay the toll.**

## 1. The machinery is executing the signal faithfully

If the rule were broken, realised P&L would differ from what the panel says the
traded region earns. It does not.

Panel, tradeable window (next open → 7 bars, the realised median hold):

| region | n | mean forward return |
| --- | ---: | ---: |
| `score > +1.5` (the long gate) | 17,175 | **−0.37 bps** |
| `score < −1.5` (the short gate) | 16,157 | **+0.74 bps** |

So the panel predicts a long side of −0.37 and a short side of −0.74 (shorting
something that rises), averaging **−0.55**.

Realised, `m5_mine`:

| side | n | gross | hold | hit |
| --- | ---: | ---: | ---: | ---: |
| LONG | 3,137 | −0.08 | 9.4 bars | 0.363 |
| SHORT | 2,978 | −1.06 | 9.0 bars | 0.358 |
| **combined** | 6,115 | **−0.56** | median 7 bars | 0.361 |

**−0.56 realised against −0.55 predicted.** The thresholds, the exits, the ATR
stop and the book are not leaking anything; the rule is faithfully harvesting a
signal that points the wrong way. Only 3% of positions reach the 30-bar cap, so
the holding period is set by the fade exit, not the limit.

## 2. The effect *strengthens* in the tail, which is the signature of a real one

Mean forward return **in the score's own direction**, by score magnitude:

| \|score\| | n | return in the score's direction |
| --- | ---: | ---: |
| 0.5 – 1.0 | 348,880 | −0.06 bps |
| 1.0 – 1.5 | 124,911 | −0.25 |
| 1.5 – 2.0 | 28,752 | −0.20 |
| **2.0 – 3.0** | **4,560** | **−2.72** |

A badly defined or noisy factor gets *weaker* in its tail — extreme readings
become dominated by data errors and thin prints. This gets **45× stronger**.
That is what a well-measured relationship looks like, and the entry gate at
±1.5 is selecting precisely the region where the factors are most reliably
inverted.

## 3. Flipping the sign confirms it, in both windows

Signed weights negated (`rvol_5m` is a gate and unaffected):

| arm | split | n | gross | net | hit |
| --- | --- | ---: | ---: | ---: | ---: |
| as built (momentum) | m5_mine | 6,115 | −0.56 | −3.56 | 0.361 |
| sign flipped | m5_mine | 7,636 | +0.02 | −2.98 | **0.546** |
| **flipped, \|score\| > 2.0** | m5_mine | 1,485 | **+0.53** | −2.47 | 0.539 |
| as built (momentum) | m5_test | 5,202 | −0.09 | −3.09 | 0.358 |
| sign flipped | m5_test | 6,618 | −0.02 | −3.02 | **0.551** |
| **flipped, \|score\| > 2.0** | m5_test | 1,633 | **+0.78** | −2.22 | 0.537 |

The hit rate moves 0.361 → 0.546 the moment the sign flips, in both windows.
Restricting to the tail the panel identified turns gross positive in both:
**+0.53 and +0.78 bps**. This is the first positive gross this score has
produced, and it was predicted by the panel before it was backtested.

**It is still not tradeable.** +0.78 bps against a **3.00 bps** round trip needs
to be four times larger. Net is −2.22.

One arm is a warning worth keeping: `flipped, >2.0, hold 7` reads **+1.43 bps on
m5_mine and −0.45 on m5_test**. The holding period was chosen from m5_mine's own
realised median, and it does not survive. The threshold at 2.0 came from the
panel and does survive; the hold did not and does not.

## 4. So what would be a real answer to "are the factors bad"

They are not bad; they are **short-horizon price-impact detectors**. `ret_5m`
inverts perfectly (Spearman −1.000 at 5 minutes, R30 §3) because a name that
just moved 5 minutes' worth relative to its peers has usually absorbed an order,
not started a trend. Every price factor here is a different lens on that same
impact, which is also why they correlate 0.45 on average (R31 §3): seven views
of one event, voting seven times.

The definitional improvements worth making are therefore about **separating the
impact from whatever is not impact**, not about better momentum windows:

* replace the seven-way sum with one signed residual-momentum term plus unsigned
  confirmations, so the vote is not seven copies of one number;
* measure at a horizon where impact has decayed and any real trend remains —
  the decile spread does not grow from 5 to 30 minutes, so this probably needs
  hours, not minutes;
* and note that cross-sectional standardisation deletes the market-common move
  entirely (R31), so none of this speaks to index-level trend.

## 5. Recorded

Nothing was promoted. The flipped configuration is not committed: it is a
mining-split observation that replicated once on `m5_test`, and `m5_test` now
carries enough trials that a fresh window is needed before it means anything.
