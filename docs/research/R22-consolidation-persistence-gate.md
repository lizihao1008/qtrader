# R22 — The persistence model as a gate: is there a tradable edge?

**Date:** 2026-09-07
**Question:** `market_state` shipped a conditional model on top of the 横盘
detector. Re-run the backtest. **Can this produce a usable edge in real
trading?**

**Answer: no. Not with this model, and not with a perfect version of it in one
of the two windows tested.** The realistic gate leaves every arm net-negative;
a look-ahead oracle that knows the true answer clears the toll by 0.47 bps per
round trip in the single most favourable window, which is smaller than the known
error in the cost model.

## 1. What changed upstream

The rule detector is unchanged — the binary gate of R21 reproduces to the digit,
which is the check that confirms it. The addition is
`consolidation_model`: a GBDT estimating

> P(all of the next 6 closes stay inside the current frozen buffered box)

reported by its own repo at **AUC 0.7292**, PR-AUC 0.6730, ECE 0.0154 on 203,044
out-of-sample rows from an expanding 12-fold walk-forward with purge and
embargo. That number is theirs, properly measured, and is not in dispute here.

**This experiment uses their walk-forward `predictions_gbdt.parquet`, never the
`final_*.joblib` fit**, which saw every row and would be in-sample across most of
any backtest window.

**Universe.** The model's OOS coverage stops for CVX/HD/UNH in 2026, so the test
runs on `config/universe/consolidation_ml_12.yaml` — the twelve names covered
throughout. A gate inert on half the book is a book-capacity experiment (R17),
not a gate experiment. Everything else is inherited from
`sr_momentum_5min.yaml` unchanged: **3.00 bps per round trip.**

**Timing.** A prediction row stamped `t` carries `decision_time = t + one bar`:
built from bars `<= t`, knowable when `t` closes — exactly when this repo decides
on bar `t`. The adapter asserts the lag is uniform rather than assuming it,
because a silent off-by-one here is indistinguishable from an edge.

## 2. The gate, swept

Veto a new entry while `P(hold) >= threshold`. NaN means *no box*, which is not
a veto.

| arm | `last_year` | `m5_validate` | `m5_test` |
| --- | ---: | ---: | ---: |
| baseline gross | +0.58 | +1.56 | +2.38 |
| veto P >= 0.50 | +0.69 | +1.15 | +2.22 |
| **veto P >= 0.60** | **+0.83** | +1.52 | **+2.58** |
| veto P >= 0.70 | +0.71 | +1.57 | +2.36 |

bps per round trip, notional-weighted, gross. **Cost is 3.00.**

The best threshold (0.60) is a **selected maximum over five**, and it does not
repeat: on `m5_validate` it is *worse* than the baseline. 0.50 is worse than
baseline in all three windows. Net per trade at the best arm: −2.17 / −1.48 /
−0.42 bps. No arm in any window is net positive.

## 3. Does P(hold) rank trades at all?

Rank IC of `P(hold)` against the baseline's own gross return per trade, on the
trades decided inside a tracked box:

| split | rank IC | n | session-clustered permutation |
| --- | ---: | ---: | ---: |
| `last_year` | −0.0929 | 393 | p = 0.252 |
| `m5_validate` | −0.0537 | 292 | p = 0.287 |
| `m5_test` | −0.1078 | 266 | p = 0.331 |

The sign is negative in all three — the *expected* direction, since entering a
breakout while the box is likely to hold should be worse. But `last_year`
overlaps both other windows, so there are two independent observations, not
three, and neither is distinguishable from zero (Fisher combination of the
independent pair: p ≈ 0.32). Terciles on `last_year` are not monotone either
(−0.20, +1.82, −1.99 bps), which is the shape of noise rather than of an effect.

An |IC| of 0.05–0.11 is at most ~1% of the variance of a trade's outcome.

## 4. The oracle bound — the number that answers the question

Replace the prediction with the **realised label**: veto exactly those boxes
that did in fact hold. This is look-ahead by construction and cannot be traded.
It exists to bound what *any* model of this target could ever be worth.

| arm | `m5_test` gross | net | total return |
| --- | ---: | ---: | ---: |
| baseline | +2.38 | −0.62 | −1.36% |
| model, best swept threshold | +2.58 | −0.42 | −0.90% |
| **ORACLE: perfect box persistence** | **+3.47** | **+0.47** | **+1.01%** |

| arm | `last_year` gross | net | total return |
| --- | ---: | ---: | ---: |
| baseline | +0.58 | −2.42 | −7.67% |
| model, best swept threshold | +0.83 | −2.17 | −6.82% |
| **ORACLE** | **+1.76** | **−1.24** | **−3.86%** |

Perfect foresight about box persistence is worth **+1.09 to +1.18 bps** of gross.
The shipped model captures about 20% of that (+0.21 to +0.25), which is what an
AUC of 0.73 against a 0.44 base rate should deliver — the model is performing as
advertised on its own target.

**The ceiling is the problem, not the model.** With perfect knowledge the
strategy earns +0.47 bps net per round trip in `m5_test` and is still −1.24 bps
in `last_year`. The favourable case is +1.01% over eight months on 1,527 round
trips — below a T-bill, from an unattainable oracle, before queue position,
partial fills, borrow, or impact.

And C00 §3b records that the flat 3.00 bps cost model is roughly **5x optimistic
for stocks at the open**, where this strategy concentrates its entries. The
oracle's entire margin is 0.47 bps. It is smaller than the known error in the
cost assumption it is measured against.

## 5. Why the target is the wrong one

The model answers "will this box hold". The strategy needs "will this trade
make money". Those coincide only if breakouts from boxes that break are
profitable, and R04/R05/R18 have measured the continuation and reversal
statistics on this data at rank IC −0.023 and +0.0001. A perfect regime
classifier feeding a rule with no edge produces a well-timed rule with no edge.

Nothing in the gating layer can fix that. The binding constraint has not moved
since R19: **no candidate score with measurable IC, against a cost model that is
flat when real spreads are not.**

## 6. Recorded

Two trials appended to `results/search/ledger.jsonl` (`m5_test`, `m5_validate`),
both REJECTED. `m5_test` now carries 5 trials and `m5_validate` 4; the
Bonferroni burden on any future claim from those splits rises accordingly.
