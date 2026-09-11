# R12 — The settled configuration, tested once on `m5_validate`

**It did not replicate.** Gross per trade fell from +4.33 bps to +1.36, total
return from +4.86% to −5.24%, and the short leg — the stronger of the two on the
mining window — inverted.

`m5_validate` has now been spent. Trial 1 against it; there is no second try.

---

## 1. What was tested

One configuration, no variants, exactly as `config/backtest/sr_momentum_5min.yaml`
stood after R10–R11:

    use_previous_day    false        require_retest    false
    use_opening_range   true         retest_bars       6
    use_round_numbers   true         momentum_z_min    1.0
    level_atr           0.15         rvol_min          1.0
    atr_window          24           require_vwap_side true
    sizing              equal        stop_sigmas       1.0
    max_weight          0.15         trail_sigmas      1.5
    max_positions       6            horizon_bars      12
    momentum_estimator  session      no_entry_after    15:00
    costs               1.0 bps half-spread + 0.5 slippage = 3.00 round trip

No LLM layer, no Kronos, no give-back cap, no reversal exit.

Execution audit on the window passes all five checks over 4,150 fills — next
bar's open, prices inside `[low, high]`, spread always adverse, silent-bar fills
all declared stale exits, and 2,200 earlier fills unchanged when every bar after
2025-08-15 was tripled.

## 2. The result

| | `m5_mine` (in sample) | `m5_validate` (out of sample) |
| --- | --- | --- |
| window | 2024-01-02 → 2025-04-01 | 2025-04-01 → 2026-01-01 |
| trades | 2,761 | 2,079 |
| **gross / trade** | **+4.33 bps** | **+1.36 bps** |
| **t** | **+1.86** | **+0.50** |
| net / trade | +1.33 | −1.64 |
| hit rate | 44.5% | 40.5% |
| **total return** | **+4.86%** | **−5.24%** |
| Sharpe | +0.44 | −0.51 |
| max drawdown | −8.60% | −18.44% |
| turnover | 2.50x/day | 3.09x/day |

Gross per trade retained **31%** of its in-sample value. At t = +0.50 the
out-of-sample gross is indistinguishable from zero, and this is before the cost
correction of R10 §5, which applies to this window as it did to the other.

## 3. How it failed is more informative than that it failed

**The leg structure inverted.**

| | `m5_mine` | `m5_validate` |
| --- | --- | --- |
| long | +3.77 bps, 46.8% hit | **+5.61 bps**, 44.4% hit |
| short | **+4.91 bps**, 44.7% hit | **−3.10 bps**, 39.6% hit |

The short leg carried the in-sample result and is the larger loser out of
sample, a swing of −8.0 bps per trade. A real structural effect does not change
sign; a sample artefact does. This is the single clearest piece of evidence in
the whole exercise that the in-sample number was noise.

**The session profile survived, which rules out one alternative explanation.**

| bar of session | `m5_mine` share / gross | `m5_validate` share / gross |
| --- | --- | --- |
| 0–1 | 57% / +6.68 | 50% / +4.26 |
| 2–5 | 12% / −1.04 | 7% / −20.42 |
| 6–11 | 4% / +2.26 | 7% / +3.96 |
| 12–23 | 7% / −6.55 | 10% / −5.64 |
| 24+ | 20% / +4.84 | 26% / +3.71 |

Half the trades are still decided in the first ten minutes and that bucket is
still the largest positive contributor. So the failure is not "the strategy
stopped trading the open" — it traded the same way and the edge was smaller.
Which also means R10 §5's cost finding applies here unchanged: the surviving
+1.36 bps is concentrated exactly where the flat 1.0 bps half-spread is most
optimistic.

## 4. What this settles

The configuration was assembled over roughly 33 recorded trials against
`m5_mine`, with a best in-sample t of +1.94 against a Bonferroni bar near 3.3.
The out-of-sample result is what that arithmetic predicted. Nothing about it is
surprising; it is the confirmation, at the cost of the window.

* `sr_momentum` should not be carried forward.
* The individual findings behind it remain valid, because they were measurements
  rather than selections: **sizing anti-correlated with the edge** (R10 §5h),
  the **flat cost model against a 14x intraday spread curve** (R10 §5), the
  **retest never binding** (R10 §5g), and the **session drift estimator unable
  to reverse** (R10 §5c). Those are properties of the code and the data, not of
  a parameter choice, and they will matter for whatever is built next.
* `m5_test` remains untouched. It should stay that way until something is
  positive on a window it was not fitted to — which now requires a new mining
  window, since `m5_mine` and `m5_validate` are both spent.

## 5. What was not done

The LLM layer was not run here, by instruction and correctly: a filter can only
narrow this set, and narrowing a set whose gross is +1.36 bps at t = +0.50
cannot produce an edge. It would also have spent the window on two hypotheses
instead of one.
