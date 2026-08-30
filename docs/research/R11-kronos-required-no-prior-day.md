# R11 — Kronos agreement required, previous-day bars removed

**Two instructions, both implemented in full:**

1. an entry fires only when the Kronos forecast points the same way as the
   technical judgement;
2. no previous-day K-line may influence the decision.

Both are now the config default. This records what they cost, because the
measurement is the point of doing it rather than arguing about it.

---

## 1. Removing previous-day bars

`use_previous_day: false` removes the PDH/PDL level family. Auditing for other
routes, one more was found and fixed:

**`average_true_range` carried the previous close across the session boundary.**
Today's first bar's true range was therefore `|high − yesterday's close|`,
dominated by the overnight gap — and ATR sets `level_zone`, the width that
decides what counts as a break. Yesterday's price was setting today's break
tolerance. `average_true_range` now takes a `restart` argument; the first bar of
a session has a true range of `high − low`. Pinned by a test.

Three things still use more than one session, deliberately:

| | uses | why it stays |
| --- | --- | --- |
| `relative_volume` | 5-session rolling median of dollar volume | a **scale**, not a level |
| `seasonal_volatility` | per-minute profile from completed prior sessions | a **scale** |
| Kronos context | 128 bars ≈ 1.6 sessions ending at the decision bar | the model needs history |

The distinction: yesterday's *high* is a price the strategy trades against, a
substantive directional input. A multi-day volatility or volume estimate is a
normalisation and points nowhere. Restricting these to one session leaves
nothing to normalise against for the first hour — where most entries are — and
would leave Kronos with almost no context for the 58% of candidates at bar 0–1,
which directly fights instruction 1.

**Effect, before the Kronos requirement** (with equal sizing and break entry
from R10 §5g–5h):

| | trades | gross/trade | t | net | total return | Sharpe | hit rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| with previous-day levels | 2,750 | +4.27 | +1.85 | +1.27 | +4.56% | +0.41 | 45.5% |
| **without, ATR session-local** | 2,761 | **+4.33** | +1.86 | +1.33 | **+4.86%** | +0.44 | 45.7% |

Removing them is a small improvement. It is also the removal of an input R07
had already measured as null, so this is consistent rather than surprising.

## 2. Requiring Kronos to agree

50,155 candidate bars scored — every bar where a break is live is a place an
entry could fire, and vetoing one changes which others get position slots, so
the whole set has to be scored for the comparison to be honest.

| | trades | gross/trade | t | net | total return | Sharpe | maxDD | hit rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **no filter** | 2,761 | **+4.33** | +1.86 | +1.33 | **+4.86%** | +0.44 | −8.60% | 45.7% |
| Kronos must agree | 2,923 | +1.22 | +0.61 | −1.78 | **−7.49%** | −0.65 | −14.15% | 43.0% |
| Kronos ≥ 0.25σ | 2,922 | +0.73 | +0.41 | −2.28 | −9.34% | −0.89 | −15.31% | 40.7% |

Verified binding: all 40 panels in `setups_kronos_required.html` carry an
agreement mark, none a disagreement.

### Why it hurts, when the raw conditional does not

Split the trades the strategy takes **without** the filter by what Kronos said
about them:

| | trades | share | hit rate | gross/trade |
| --- | --- | --- | --- | --- |
| Kronos disagreed | 1,772 | 65% | 45.0% | +2.72 bps |
| Kronos agreed | 951 | 35% | 47.0% | **+6.84 bps** |

Agreed minus disagreed: **+4.12 bps, t = +0.83.** On this candidate set the
model looks considerably more informative than it did in R09 (+0.28 bps,
t = +0.06) — but still short of significance, and the sign of the *rule* built
from it is the opposite of the sign of the conditional.

The gap is a **book-capacity effect**, and it has now appeared three times in
this project — with the session-open blackout (R10 §3), with the R09 filter, and
here. `max_positions = 6` binds. A veto therefore does not remove an
opportunity; it frees a slot, and `_respect_book_limit` fills it with the next
candidate, which is by construction weaker. Trade count *rises*, 2,761 → 2,923,
and turnover with it, 2.50 → 2.64x/day.

Testing that directly, by widening the book so a veto removes trades instead of
reshuffling (12 slots at 0.075 weight — same gross exposure):

| | trades | gross/trade | t | total return |
| --- | --- | --- | --- | --- |
| wide book, no filter | 5,762 | +1.77 | +1.21 | −4.82% |
| wide book, Kronos required | 4,974 | +0.51 | +0.38 | −8.41% |

The filter is still worse. And the control shows something else worth keeping:
**widening the book is itself very costly**, +4.86% → −4.82%. The six-slot cap
was not just a risk limit — `_respect_book_limit` ranks candidates by
`|momentum_z|` and keeps the strongest, so it was doing real selection.

## 3. Conclusion

Both instructions are implemented, enforced and now the config default.

* Removing previous-day bars: **small improvement**, +4.56% → +4.86%, and it
  closes a genuine leak in the ATR.
* Requiring Kronos agreement: **+4.86% → −7.49%**, hit rate 45.7% → 43.0%, max
  drawdown −8.60% → −14.15%.

The raw conditional (+4.12 bps for agreement, t = +0.83) is the strongest sign
of life Kronos has shown in this project, and it is still not significant. It
does not survive being turned into a rule, in either book configuration.

If the requirement is to stand, the honest statement is that it costs about 12
percentage points of total return on `m5_mine` and is retained on judgement
rather than on evidence. The alternative that the numbers would support is to
use the score as a **ranking input** to `_respect_book_limit` rather than as a
veto — it would then compete for the six slots instead of freeing them. That has
not been tested and would be a new hypothesis, not a variant of this one.

`m5_mine` has now carried **33 recorded trials**; Bonferroni at α = 0.05 puts the
bar near |t| = 3.3. R10 §5's cost finding applies to every row above unchanged.
