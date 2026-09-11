# R16 — Lower momentum threshold, confirmed on 1-minute bars

**Date:** 2026-09-02
**Strategy:** `sr_momentum`, 5-minute structure + 1-minute confirmation, `m5_mine`
**Instruction:** the momentum threshold is too high; also use 1-minute bars as a
joint confirmation. Many trends are over quickly on a 5-minute scale — find the
best trade-off.

**Result: half the instruction is supported and half is not.** Lowering the
coarse threshold helps and is worth keeping. The 1-minute confirmation carries
**no information at all** about these entries — rank IC +0.0016 at t = +0.22
over 20,711 candidates — and the apparent variation across its thresholds is
noise. Nothing reaches a positive expectancy.

## 1. Why the threshold was the right thing to question

Traced from the pictured TSLA short (2025-03-17). Opening-range support 238.94
broke at 10:25 ET. The entry did not fire until **11:40** — 15 bars later, and
five bars before the session low.

`momentum_z` at the break was **−0.40** against a −1.00 threshold, because
`session_drift_zscore` measures drift *since the open*, not direction: TSLA
opened at 239.60, ran **up** to 243.96, then fell. At 10:25 price was 237.34,
only −95 bps from the open, while the previous six bars were −101 bps and the
drop from the 09:50 high was −275 bps. The early rally was cancelling the
decline inside the statistic.

The threshold only cleared once *cumulative* drift reached −229 bps, by which
point most of the move had happened. That is the mechanism behind "趋势快走完了
才入场", and it is a property of the estimator, not of the threshold alone.

## 2. What was built

`features/multiframe.py` — the one place the two grids meet, so the causality
rule lives there and is asserted in its own test file.

> A coarse bar labelled `T` spans `[T, T+step)` and **closes at `T+step`**. The
> strategy decides at that close, so the fine bars in existence are `T..T+step-1`.
> Each coarse bar takes the **last** fine observation inside its own span.

Taking the fine bar labelled `T+step` instead would hand the strategy the first
minute of the bar it is about to be filled on, and nothing downstream would
notice. A test perturbs every fine bar from a cut point forward and asserts no
earlier coarse value changes.

`MarketContext.fine_panel` and `DataConfig.fine_timeframe` are both optional and
default to nothing, so every existing strategy and config is unaffected. A bar
where the fine grid has no reading is a **refusal**, not a pass.

1-minute bars did not cover `m5_mine` at all (coverage began 2026-02), so
**4,632,341 bars across 31 symbols** were downloaded for the window first.

Execution audit on the new path passes all five checks, including that tripling
every bar after 2024-08-13 leaves all 4,192 earlier fills identical.

## 3. Lowering the coarse threshold alone

| coarse `momentum_z_min` | trades | gross/trade | t | total return |
| ---: | ---: | ---: | ---: | ---: |
| **1.0** (canonical) | 4,037 | **+0.64 bps** | +0.51 | −12.80% |
| 0.75 | 4,161 | +1.40 | +0.94 | −9.30% |
| 0.50 | 4,247 | +1.30 | +0.96 | −10.16% |
| **0.25** | 4,236 | **+1.80** | +1.36 | **−7.38%** |

Gross per trade roughly triples. The instruction was right: the high threshold
was discarding the early part of every move it eventually traded.

## 4. Adding the 1-minute confirmation

| coarse | fine | trades | gross/trade | t | total return | max DD |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1.00 | 0.0 | 4,010 | +0.53 | +0.42 | −13.31% | −15.66% |
| 1.00 | 0.5 | 3,912 | +0.78 | +0.61 | −11.80% | −14.63% |
| 1.00 | 1.0 | 3,371 | +0.50 | +0.39 | −11.42% | −13.59% |
| 0.50 | 0.0 | 4,241 | +1.46 | +1.14 | −9.23% | −13.70% |
| 0.50 | 0.5 | 4,143 | +0.88 | +0.68 | −11.99% | −15.50% |
| **0.50** | **1.0** | 3,651 | **+1.83** | +1.40 | **−6.12%** | **−9.30%** |
| 0.25 | 0.0 | 4,259 | +1.76 | +1.35 | −7.61% | −12.41% |
| 0.25 | 0.5 | 4,202 | +0.95 | +0.74 | −11.82% | −15.55% |
| 0.25 | 1.0 | 3,756 | +1.11 | +0.87 | −9.76% | −12.84% |

The best cell is 0.50 / 1.0. It is also **statistically identical to coarse 0.25
with no fine filter at all** (+1.83 against +1.80), which already says the fine
data is not what produced it.

Two things rule out the filter properly:

**The fine dimension is non-monotonic at every coarse level.** Going 0.0 → 0.5 →
1.0 gives +1.46 / +0.88 / +1.83 at coarse 0.50 and +1.76 / +0.95 / +1.11 at
coarse 0.25. The middle setting is worst in both. A filter that carried
information would not zig-zag.

**Measured directly, it carries none.** Taking the candidate set with the fine
gate *off* — so the sample is not selected by the thing under test — and scoring
the 1-minute drift against each trade's own realised forward return:

    rank IC  +0.0016   (t = +0.22, n = 20,711)

| 1-minute agreement | trades | forward return | hit rate |
| --- | ---: | ---: | ---: |
| most against | 4,143 | +0.17 bps | 49.6% |
| 2 | 4,142 | +1.06 | 49.8% |
| 3 | 4,142 | +0.53 | 50.0% |
| 4 | 4,142 | +0.03 | 49.3% |
| most agreeing | 4,142 | +0.57 | 49.4% |

Flat. Knowing how strongly the 1-minute grid agreed with an entry tells you
nothing about whether it worked.

## 5. Conclusion

* **Keep the lower threshold.** `momentum_z_min: 0.25` is a real improvement over
  1.0 on gross per trade (+0.64 → +1.80) and on drawdown, and it is explained by
  a mechanism rather than found by search.
* **Do not adopt the 1-minute confirmation.** It is a measured null. This is the
  same outcome as Kronos (R09, IC −0.011) and the LLM veto: a second opinion
  that has no information cannot improve a first one.
* **Nothing here is tradable.** The best gross is +1.83 bps against a configured
  3.00 bps round trip, and R10 §5 shows even that 3.00 is optimistic where these
  trades happen. The best total return in the grid is −6.12%.

The trade-off the instruction asked for does exist and was found — enter earlier
by lowering the coarse gate — but the thing meant to pay for the lost strictness
does not work. The strictness was not buying anything either, which is why
removing it helped.

Nine grid cells were run against `m5_mine`, on a window that has now carried
roughly 40 trials. Treat the best cell accordingly.
