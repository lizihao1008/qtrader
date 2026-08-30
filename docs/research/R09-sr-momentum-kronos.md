# R09 — S/R break-and-retest + momentum, cross-validated by Kronos

**Request:** 保留 S/R 判断，加上追动量的策略，然后找到入场机会后使用 Kronos 交叉验证 —
build it and let the test decide.

**Result: built, audited and measured. The strategy is flat-to-negative, and
Kronos confirmation makes it strictly worse at every threshold tested.**
The specific hope behind the request — that a model with a ">50% win rate"
would filter out the S/R signal's misjudgements — is falsified: the filter
removes winners faster than losers, and the hit rate falls monotonically as it
is tightened.

R08 predicted this on arithmetic. It was implemented anyway because the
instruction was to let results decide, and because R08's argument had a testable
consequence that had not been tested. It now has been.

---

## 1. What was built

`sr_momentum` (`src/qtrader/strategies/sr_momentum.py`), 5-minute bars, the
22-name liquid US universe, `m5_mine` (2024-01-02 → 2025-04-01).

    break        close beyond an ex-ante level by more than max(1 tick, 0.15 x ATR)
    retest       price returns into the zone around that level within 6 bars
    hold         and closes back on the breakout side
    momentum     session drift z-score >= 1.0, sign matching the break
    participation relative dollar volume >= 1.0
    context      price on the breakout side of the session VWAP
    exit         monotone ratchet: max(entry - 1.0 sigma_H, extreme - 1.5 sigma_H)
    discipline   no entry after 15:00, flat at 15:50, max 6 positions

Levels are all **ex-ante** (`src/qtrader/features/levels.py`): previous-day
high/low, opening-range high/low (unavailable until the range has closed), and
round numbers. Drawing levels on a finished chart is the easiest way to
manufacture an untradable backtest, so none of them may look forward.

Parameters are the deep-research report's stated starting values, not search
results. No parameter was tuned before the numbers below were produced.

### A defect the tests caught

`nearest_round_levels` returned the round numbers bracketing the **current**
close. `price > resistance` is then unsatisfiable by construction, so the entire
round-number family was inert and contributed nothing. The reference is now the
previous bar's brackets — which is also the only version knowable before the bar
prints. Candidates went 5,762 → 15,646 and the first baseline was void. That
baseline (+2.72 bps gross) should be disregarded; only the numbers below stand.

This is worth recording because it is the failure mode that flatters results
rather than harming them: a silently dead component makes a strategy look like
it works on fewer moving parts than it actually has.

### Execution audit

`scripts/audit_execution.py` on `m5_mine`, 5,540 fills, all five checks pass:

* fill reference prices equal each bar's open, one bar after the signal;
* every reference price lies inside its bar's `[low, high]`;
* every fill pays the spread adversely ($7,988 total);
* the 21 fills on bars with no print are all declared stale exits;
* tripling every bar after 2024-08-13 left all 2,839 earlier fills identical.

That last one is the leakage test that matters: the past does not move when the
future changes.

---

## 2. Baseline, before Kronos

`m5_mine`, 2,788 round trips, 15 months.

| | all | long | short |
| --- | --- | --- | --- |
| trades | 2,788 | 1,450 | 1,338 |
| hit rate | 44.1% | 44.4% | 43.8% |
| gross / trade | **+3.50 bps** | +2.17 | +4.95 |
| cost / trade | 3.00 bps | 3.00 | 3.00 |
| expectancy | +0.50 bps | −0.83 | +1.95 |
| profit factor | 0.97 | 0.95 | 0.99 |

Total return −3.02%, Sharpe −0.47, max drawdown −8.95%, turnover 1.80x/day,
mean hold 48 bars.

**t = +1.51 on 2,788 trades.** That is indistinguishable from zero. The
strategy is not profitable and it is also not measurably unprofitable; it is
consistent with having no edge at all, which is exactly what R07 predicted for
these level families and what R04/R05 predicted for intraday momentum here.

Two details worth naming rather than glossing:

* **Per-trade expectancy is positive (+0.50 bps) while the capital-weighted
  return is negative (−3.02%).** The losers sit in the larger positions. An
  equal-weighted expectancy that disagrees with the equity curve is a sizing
  artefact, not an edge.
* The cost model is 1.0 bps half-spread + 0.5 bps slippage = 3.0 bps round trip.
  The Roll estimate on 1-minute data for this universe is 0.68 bps half-spread,
  so this is conservative, not optimistic. Even at zero cost the strategy is a
  +3.50 bps/trade signal at t = +1.51 — still not significant.

---

## 3. Kronos as the confirmation filter

`Kronos-small` (24.7M) + `Kronos-Tokenizer-base`, running locally on MPS.
Context window **128 bars ending at the candidate bar inclusive**; forecast
`pred_len = 12` bars beginning after it. 15,646 candidates scored in 10 minutes
at ~26 windows/s. The score is the forecast's implied return in units of the
symbol's own horizon volatility:

    score = log(forecast_close[-1] / last_close) / (sigma * sqrt(pred_len))

Distribution over the candidate set: mean −0.046, sd 1.11, 48.6% positive.

The window boundary is asserted in a unit test — the model must never see the
bar it is predicting.

### 3.1 The direct test: does the score predict anything?

This is cheaper and cleaner than a backtest, because it asks the question
without position limits, sizing and exits in the way. Forward return is measured
from the price the strategy would actually pay (next bar's open) to the close
`H` bars later, signed by the proposed direction.

| horizon | n | directional agreement | **rank IC** |
| --- | --- | --- | --- |
| 12 bars | 15,645 | 49.3% | **−0.0108** (t = −1.35) |
| 48 bars | 15,624 | 49.1% | **−0.0173** (t = −2.16) |

**Kronos agrees with the realised direction 49% of the time.** Not 50-something
percent — a coin flip, and if anything a fractionally wrong-sided one. The rank
IC is zero at 12 bars and marginally negative at 48.

A filter with no rank IC against the outcome cannot improve a backtest except by
luck. Everything in §3.2 follows from this line.

### 3.2 The backtest, with and without

Identical candidate set; the filter only vetoes.

| variant | trades | hit rate | gross/trade | t | expectancy | total return | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **no filter** | 2,788 | **44.1%** | **+3.50 bps** | +1.51 | +0.50 bps | −3.02% | −0.47 |
| Kronos agrees (≥0) | 2,483 | 41.4% | +1.13 | +0.53 | −1.87 | −7.37% | −1.22 |
| Kronos ≥ 0.25σ | 2,075 | 40.6% | +1.33 | +0.62 | −1.67 | −7.04% | −1.28 |
| Kronos ≥ 0.50σ | 1,472 | 40.4% | +1.29 | +0.53 | −1.71 | −2.58% | −0.53 |

**The hit rate falls monotonically as the filter is tightened: 44.1 → 41.4 →
40.6 → 40.4%.** Gross per trade drops by roughly two thirds on the first
application and does not recover. The short leg, which was the only positive
sleeve at +4.95 bps gross, is turned negative (−1.29 bps) by the filter.

This is the direct answer to the question that prompted the work. The filter
does not remove S/R misjudgements. It removes trades roughly at random with a
slight bias toward removing the good ones, which is what a signal with a
fractionally negative IC does.

---

## 4. Why the ">50% win rate" claim did not transfer

The claim is not necessarily false where it was made. It does not apply here,
for reasons that were knowable in advance:

* **Different horizon and different bar.** Directional-accuracy claims for K-line
  foundation models are typically on daily or hourly crypto bars, where a single
  bar's move is many multiples of the spread. Here the 12-bar move is ~3 bps of
  signal against a 3 bps round trip.
* **A win rate is not an edge.** At this hold length the breakeven hit rate is
  in the low 40s only because the ratchet exit truncates losers. A 51% win rate
  on symmetric outcomes with a 3 bps toll is a losing strategy.
* **Selection.** The claim is quoted about the model's own examples, chosen
  after the fact. The authors' README states plainly that Kronos is not a
  production-ready trading system and makes no profitability claim.
* **Conditioning on A does not rescue B.** R08's argument: if `A` (the S/R
  setup) carries no information about `r`, then `E[r | A ∧ B] = E[r | B]`. The
  measurement now confirms both halves — `A` is uninformative (t = +1.51) and
  `B` is uninformative (IC ≈ 0) — so the conjunction is uninformative on a
  smaller sample.

---

## 5. What was not done, deliberately

**`m5_validate` was not touched.** The rule in this project is that a variant
goes to the confirmation window once, after it looks clearly positive on the
mining window. Nothing here is positive on `m5_mine`, so spending the
out-of-sample window would destroy a scarce resource to confirm a result that is
already negative in-sample.

**The filter was not inverted.** The rank IC is negative, so flipping the veto
would improve the backtest. On a t of −1.35 over one window, after the filter
direction was chosen by looking at that window, that is a data-mined sign flip
and nothing else. It is exactly the procedure the falsification-first mandate
exists to prevent.

**No parameters were searched.** `level_atr`, `retest_bars`, `momentum_z_min`,
`rvol_min` and the ratchet geometry are all at their stated starting values. The
result is a clean single test, not the best of many.

`m5_mine` has now carried 15 recorded trials. Anything found on it in future
carries that multiple-testing burden.

---

## 5b. What the trades actually look like

`scripts/plot_setups.py` renders the 20 best and 20 worst round trips as
real-price panels, each carrying the level the rule was watching, the direction
it predicted, and the close path Kronos forecast from the same decision bar
(`results/sr_momentum_5min__m5_mine/setups.html`).

Three things are visible there that the summary statistics do not convey.

**The level is real, the reaction is not.** Every panel has a genuine S/R line
with price breaking and retesting it — the setup is not an artefact. Winners and
losers are indistinguishable in the bars before the entry. That is the picture
of a signal with no edge: the pattern is there, and it does not condition the
outcome.

**Kronos disagrees with the strategy about as often on winners as on losers.**
Across the 40 charted panels the model agreed with the direction taken on 8 of
20 winners and 6 of 20 losers. A 10-point gap on 40 hand-picked extremes is
noise, and it points the same way as the −0.011 rank IC measured over all
15,645 candidates: no separation.

**The forecast horizon and the holding period do not match.** Kronos was asked
for 12 bars; the mean hold is 48. On the panels the dashed forecast path
occupies a fraction of the trade it is being used to authorise. Lengthening the
forecast would not fix this — §3.1 measures the score against a 48-bar outcome
too, and the IC is −0.017 there — but it is a structural mismatch worth naming
rather than hiding: the filter was never looking at the horizon the position was
held over.

The panels are selected **by outcome**, so the ✓/✗ tally on them is an
illustration, not a measurement. The measurement is §3.1.

---

## 6. Conclusion

The strategy was implemented in full, faithfully to the request, and audited to
the same standard as everything else in this repository. The test decided:

1. **S/R break-and-retest + momentum has no measurable edge** on this universe
   at 5 minutes — +3.50 bps gross at t = +1.51 over 2,788 trades, against a
   3.00 bps round trip, ending at −3.02% and Sharpe −0.47.
2. **Kronos has no directional information about these entries** — 49%
   agreement, rank IC −0.011 to −0.017.
3. **The conjunction is worse than either alone**, and worse monotonically in
   the filter's strictness.

Neither component should be carried forward. The reusable output of this work is
the infrastructure, not the strategy: the ex-ante level features, the candidate
publishing convention that lets any heavy external model be scored once and
cached, and `scripts/analyze_confirmation.py`, which answers "does this filter
know anything" in seconds instead of a backtest.

That last tool generalises. Any future confirmation model should be run through
it **before** a backtest is written. If the rank IC against candidate outcomes
is zero, there is nothing for a backtest to find.
