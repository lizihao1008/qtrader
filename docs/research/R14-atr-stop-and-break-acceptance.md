# R14 — ATR initial stop and post-break acceptance

**Date:** 2026-09-02  
**Strategy:** `sr_momentum`, 5-minute IEX bars, `m5_mine`  
**Question:** can a Turtle-style ATR stop and one bar of follow-through avoid
the short, false-looking breakouts visible in the failure gallery?

## 1. The pictured AAPL trade

The AAPL short on 2024-08-06 was decided at 09:40 ET and filled at 09:45. It
was a direct break of round-number support at 203, not an opening-range break:

- close 201.665, momentum z-score −1.21, relative volume 2.13, below VWAP;
- `require_retest: false`, so no retest was needed;
- the strategy has no golden/death-cross entry condition;
- session MACD was already bearish, so a simple death-cross filter would have
  admitted rather than rejected this trade.

The old initial barrier was one horizon sigma, about 5.47 ATR in this case. It
therefore held the short until 12:45 and lost −355 bps gross. The problem was
both an unconfirmed break and an initial stop far wider than the local range.

## 2. Changes, fixed before measurement

Two independent parameters were added:

1. `acceptance_bars: 1`: the break bar cannot confirm itself. The next completed
   bar must close farther in the breakout direction while remaining beyond the
   watched level. A flat/adverse close resets the consecutive count.
2. `initial_stop_atr: 2.0`: the initial loss distance is the tighter of
   `stop_sigmas * horizon_sigma` and `2 * ATR`. The existing
   `trail_sigmas * horizon_sigma` ratchet is unchanged, so the ATR rule limits
   initial loss without shortening the trailing leash of an established trend.

Both defaults remain backward-compatible in code (`0` and `None`); the canonical
5-minute config explicitly enables `1` and `2.0`. The state is session-local and
all decisions remain close-tested, next-open-filled.

Testing the tighter stop exposed a state-machine defect: after a stop, the same
unreset break could be registered again while price remained beyond its level.
An already-traded setup is now consumed until price returns through the boundary
and genuinely re-breaks. This is a correctness rule, not a fitted cooldown.

## 3. Four-way ablation

The only tested values were the pre-stated one bar and 2 ATR. No threshold sweep
was performed. All four rows use the same `m5_mine` data and the consumed-break
correctness rule.

| variant | trades | gross/trade | gross t | hit rate | total return | Sharpe | max DD | turnover/day |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| neither | 2,809 | **+4.02 bps** | +1.74 | 44.1% | **+3.45%** | +0.33 | −10.72% | 2.54x |
| 2 ATR only | 4,754 | +1.17 bps | +0.89 | 31.7% | −12.13% | −1.00 | −17.66% | 4.30x |
| one-bar acceptance only | 3,065 | +2.05 bps | +1.19 | 42.2% | −4.38% | −0.39 | **−9.52%** | 2.78x |
| **both (canonical experiment)** | 4,037 | **+0.64 bps** | +0.51 | 34.1% | **−12.80%** | −1.29 | −15.05% | 3.66x |

The requested controls improve individual failure geometry but not the strategy.
The combined gross edge is only +0.64 bps per round trip against a configured
3.00 bps round-trip cost.

## 4. What changed in the tails

| variant | worst trade | 1st percentile | 95th percentile |
| --- | ---: | ---: | ---: |
| neither | −481 bps | −284 bps | +222 bps |
| 2 ATR only | −269 bps | −152 bps | +175 bps |
| one-bar acceptance only | −444 bps | −196 bps | +170 bps |
| both | −269 bps | **−140 bps** | +145 bps |

The controls do what they claim mechanically: the combined rule halves the 1%
left-tail loss. But it also removes or truncates the right tail, and tighter
stops release book capacity for later valid breaks. Trade count and costs rise.
This is the same capacity interaction recorded in R09–R11: a local veto/exit is
not a simple subtraction when the six-position book is usually full.

For the exact AAPL example:

- 2 ATR alone exits at 10:05 ET around 204.685, reducing gross loss from
  −355 bps to −149 bps;
- one-bar acceptance rejects the setup because the next close moves against the
  short; the combined strategy therefore has no AAPL trade that day.

That is a useful visual correction, but one selected chart is not evidence of
portfolio improvement. Across all trades the confirmation loses more large
winners than losers, while the ATR stop increases churn.

## 5. Visual and validation audit

The 20 best and 20 worst combined-strategy episodes are rendered at:

`results/sr_momentum_5min__m5_mine/setups_atr_acceptance.html`

Validation:

- 45 focused `sr_momentum` tests pass;
- 324 full-suite tests pass;
- all five execution checks pass over 8,074 fills, including the end-to-end
  future-bar perturbation test;
- `m5_test` was not read or run.

## 6. Decision

Keep both mechanisms in the implementation because they are causal, inspectable
and useful risk controls. Keep the requested values visible in the canonical
experiment config so its gallery is reproducible, but **do not deploy or call
this an improved strategy**. The ablation rejects that claim.

The next defensible improvement is not another threshold. It is a no-trade or
ranking model trained on setup quality under a time-of-day-aware cost model,
evaluated on a new mining window. `m5_mine` and `m5_validate` are already spent;
`m5_test` remains untouched.
