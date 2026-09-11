# R20 — The trailing year against simply holding the index

**Date:** 2026-09-02
**Window:** 2025-09-02 → 2026-08-28 (0.99 years), SPY + QQQ, 1.50 bps per round trip

**Result: the strategy returned −4.77% while holding the two indices equally
returned +24.27%.** The gap is 29 points. But the more useful number is the
decomposition: **essentially all of the index return over this year was earned
overnight**, and an intraday strategy that is flat by 15:50 never competes for
it.

Note the window is a calendar year, not an evaluation split. It straddles
`m5_validate` and `m5_test` by construction and must never be cited as
out-of-sample evidence.

## 1. The comparison

| | total return | annualised | Sharpe | max drawdown |
| --- | ---: | ---: | ---: | ---: |
| **strategy (intraday only)** | **−4.77%** | −4.84% | −0.79 | −7.22% |
| hold SPY | +20.87% | +21.20% | +1.51 | −9.78% |
| hold QQQ | +27.68% | +28.14% | +1.36 | −12.73% |
| hold SPY/QQQ equally | **+24.27%** | +24.67% | +1.44 | −11.25% |

The strategy is not a small bet that happened to lose: it held a position on
**72.3%** of bars at an average gross exposure of **47.7%** of equity. Scaled to
full exposure the loss is about −10%.

Its drawdown is smaller than buy-and-hold's, which is the one thing in its
favour and follows directly from being flat overnight — it cannot gap.

## 2. Where the index return actually came from

| symbol | total | **intraday** | **overnight** |
| --- | ---: | ---: | ---: |
| SPY | +20.67% | **+1.42%** | **+18.98%** |
| QQQ | +27.63% | **−1.44%** | **+29.50%** |

Open-to-close, across a full year, SPY returned **+1.4%** and QQQ returned
**−1.4%**. Close-to-open returned +19.0% and +29.5%.

This is the whole story, and it reframes the result. The strategy did not
under-perform buy-and-hold by 29 points because it traded badly. It spent the
year competing for a pool worth approximately **zero**, while the benchmark it
is being measured against collected a return available only to positions held
through the close.

An intraday strategy on an equity index is structurally excluded from the
component that produced the entire benchmark return.

## 3. What this does and does not say

**It does not say the strategy is worse than it looked in R19.** R19 measured
gross edge per trade against transaction costs, which is the right question for
whether a signal exists. This is a different question — what the capital
actually earned over a calendar year — and the answers are not in conflict.

**It does say the benchmark is the wrong one.** Comparing an intraday,
overnight-flat strategy to buy-and-hold measures the overnight drift, not the
strategy. The fair comparisons are:

* **hold the index only during the session** — the +1.42% / −1.44% column above.
  Against that, the strategy's −4.77% is a real but far smaller shortfall, and
  it is the shortfall attributable to trading.
* **any intraday alternative**, since the overnight component is unavailable to
  all of them equally.

**It also says something about where to look.** If close-to-open carried +19%
and +29% while open-to-close carried roughly nothing, the tradable structure in
this data over this year was in the overnight gap, not inside the session. This
project has spent its entire effort on the session. That is worth knowing
before choosing what to build next, and it is testable: a rule holding the index
from the close to the next open, with the same cost model, is a handful of lines
against the data already stored.

None of that rescues the current strategy, which is negative here as it was on
`m5_test`.
