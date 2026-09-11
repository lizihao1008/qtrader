# R19 — The same strategy on SPY and QQQ

**Date:** 2026-09-02
**Instruction:** run the same test on the S&P and Nasdaq ETFs at 1.5 bps of
slippage per trade; broad-index momentum may persist more smoothly.

**Result: the first net-positive numbers in this project, and they do not
survive inspection.** Two of three windows are positive, the cost assumption is
for once conservative rather than optimistic, and the book confound of R17 is
gone. But the entire result in every window comes from about five days, the
median day loses money, and the leg that produces it changes every window.

## 1. Setup

`config/backtest/sr_momentum_index_5min.yaml`. Same strategy, same parameters as
the 22-stock config, three deliberate differences:

* **universe** SPY + QQQ, both traded;
* **cost 1.50 bps per round trip** (0.25 half-spread + 0.50 slippage per side)
  rather than 3.00;
* **book sized so it cannot bind** (2 symbols, cap 2), removing the R17 effect
  where a saturated book decides which trades happen.

`round_step: 1.0` was left alone. In bps a dollar is far finer on a $554 index
than on a $138 stock, but what matters for a level is its spacing against the
bar range: **$1 is 2.38 ATR on SPY and 2.01 on QQQ**, against 1.55 for NVDA,
3.11 for AAPL and 1.21 for TSLA. The same band.

**No parameter was fitted on index data.** `momentum_z_min: 0.25` came from R16
on the stock universe, so even `m5_mine` is out-of-sample for the parameter
choice here.

Execution audit passes all five checks.

## 2. Results

| window | trades | gross/trade | t | net/trade | total return | Sharpe | max DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `m5_mine` | 929 | **+2.97 bps** | +2.02 | +1.47 | **+5.72%** | +0.71 | −6.67% |
| `m5_validate` | 584 | **+4.23** | +1.56 | +2.73 | **+6.75%** | +1.14 | −5.64% |
| `m5_test` | 450 | +0.82 | +0.40 | −0.68 | −1.45% | −0.33 | −4.18% |

## 3. The cost assumption is conservative here — a first

R10 §5 found the stock cost model optimistic by roughly 5x where most of its
trades happened. The opposite holds on the indices. Roll (1984) half-spreads on
1-minute bars:

| | half-spread | round trip | charged |
| --- | ---: | ---: | ---: |
| SPY | 0.18 bps | **0.37** | 1.50 |
| QQQ | 0.59 bps | **1.19** | 1.50 |

Breakeven cost per window, from gross alone: `m5_mine` 2.97, `m5_validate` 4.23,
`m5_test` 0.82. The first two clear the measured spread with room; the third
does not.

This is the one genuinely favourable structural difference between indices and
single stocks, and it is the reason these numbers are positive at all.

## 4. Why it is still not an edge

**The result is five days.**

| window | days | top 5 days | share of total | median day | up days |
| --- | ---: | ---: | ---: | ---: | ---: |
| `m5_mine` | 309 | +$8,035 | **141%** | −$37 | 46% |
| `m5_validate` | 188 | +$12,710 | **188%** | +$6 | 51% |
| `m5_test` | 161 | +$5,214 | — (total is −$1,454) | −$58 | 43% |

Removing the best five sessions turns every window negative, including the two
that look good. The median day loses money in two of three, and the up-day rate
is 43–51% — a coin. This is a lottery-ticket profile: a large number of small
losses paid for by a handful of outsized sessions that happened to land inside
the sample.

**The leg that produces it rotates.**

| window | SPY | QQQ |
| --- | ---: | ---: |
| `m5_mine` | +0.51 bps (t = +0.30) | **+5.56 (t = +2.31)** |
| `m5_validate` | **+5.35 (t = +1.93)** | +3.16 (t = +0.69) |
| `m5_test` | +2.81 (t = +1.14) | **−1.19 (t = −0.37)** |

QQQ carries the mining window, SPY carries the validation window, and QQQ turns
negative on the holdout. This is the same signature as R12's short-leg inversion:
a structural effect does not move between its two legs.

**The t-statistics are overstated.** Daily P&L correlation between the legs is
+0.68 / +0.59 / +0.48, so the two symbols are roughly 1.2–1.35 independent legs
rather than 2. Counting 929 trades as independent inflates t by about **1.25x**:
`m5_mine`'s +2.02 is nearer +1.6.

## 5. Verdict

The instruction's premise — that index momentum is smoother — is not
contradicted by the gross numbers, and the cost structure genuinely favours
indices. Those are real observations.

But "positive on two of three windows" here means "two windows contained a few
good days". With 43–51% up days, a negative median day, a rotating leg and a
negative holdout, there is no evidence of a persistent effect. The correct
reading is that this configuration is **not distinguishable from noise with a
fat right tail**, which is what a breakout strategy on a trending index looks
like whether or not it has an edge.

What would change the picture, in order of value:

1. **More independent instruments.** Two correlated ETFs cannot support this
   question. Sector ETFs, or index futures across regions, would give genuinely
   independent legs.
2. **A day-level test rather than a trade-level one.** With the result living in
   five sessions, per-trade t-statistics are the wrong instrument entirely.
3. **The candidate score that R18 showed is missing.** Nothing here addresses
   it; the book simply stopped hiding it.
