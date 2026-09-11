# R13 — First ten minutes are observation-only

**Date:** 2026-09-01  
**Strategy:** `sr_momentum`, 5-minute IEX bars, `m5_mine`  
**Instruction:** prohibit trading in the first ten minutes and judge entry only
after the trend is clearer.

## 1. What changed

The canonical config sets `no_entry_before: "09:35"`. Bar timestamps are bar
**open** times: the 09:35 bar closes at 09:40, and its decision is filled at the
next bar's 09:40 open. The first ten minutes are therefore observation-only and
09:40 ET is the earliest possible fill.

The gate also changed semantics. The old R10 implementation only refused the
entry while leaving the break live. A setup formed at 09:35 could therefore be
entered at 09:40 without any new event; the delay changed the timestamp, not the
information set.

The new state machine consumes every break observed while the opening gate is
closed. At the 09:40 decision/fill boundary, the strategy may trade only when:

1. price invalidates that consumed level and subsequently breaks it again; or
2. price breaks a different level after the gate has opened;
3. the existing momentum, relative-volume and VWAP-side conditions agree.

Position, break and discarded-break state is reset at every session boundary.
The engine also treats delayed targets as day orders: a target from the prior
session is cancelled instead of shifting into the next session's opening print.
This closes the early-close path that otherwise produced one 09:30 fill. Exit
geometry, sizing, ordinary execution lag and costs are unchanged.

## 2. Tests of the semantics

Focused tests establish all four required properties:

- no candidate is admitted before the cutoff;
- an opening setup cannot reappear automatically on the cutoff bar;
- returning through the level and breaking it again creates a new admissible
  event;
- leaving `no_entry_before` unset leaves the strategy unchanged.

On the completed backtest there were **zero entries before 09:40 ET**. The
earliest fill was 09:40, and 1,494 of 2,790 trades (53.5%) entered then.

## 3. Backtest result

The comparison below uses the settled no-previous-day, no-Kronos, direct-break,
equal-sizing configuration. The earlier row is the R12 in-sample reference; the
new row adds the exact ten-minute gate plus cancellation of cross-session pending
targets.

| | prior configuration | ten-minute observation gate |
| --- | ---: | ---: |
| trades | 2,761 | **2,790** |
| gross / trade | +4.33 bps | **+4.33 bps** |
| gross t-stat | +1.86 | **+1.86** |
| hit rate | 44.5% | **44.6%** |
| total return | +4.86% | **+4.69%** |
| Sharpe | +0.44 | **+0.42** |
| max drawdown | −8.60% | **−10.49%** |
| daily turnover | 2.50x | **2.52x** |

The gate is working as requested, but it does not improve alpha. The prior rule
already needed one intraday return before momentum existed, so it naturally had
almost no fill path inside the first ten minutes. Making the constraint explicit
leaves gross expectancy and its t-statistic unchanged; return is slightly lower
and drawdown slightly worse. The +4.33 bps gross remains a mined t=1.86 result,
and R10's time-of-day spread correction still makes it negative under measured
opening costs.

This is consistent with R10's earlier economic finding, but it is not the same
experiment: R10's 09:40 *decision-bar* cutoff produced a 09:45 earliest fill and
delayed live setups, effectively excluding fifteen minutes. R13 enforces the
requested ten-minute *fill* cutoff and invalidates earlier setups. Neither is
evidence of incremental alpha.

## 4. Visual audit

The standard gallery interface was run with identical rendering for both tails:

```bash
python scripts/plot_setups.py \
  --config config/backtest/sr_momentum_5min.yaml \
  --split m5_mine --n 20 \
  --out setups_open10_blackout.html
```

Output:
`results/sr_momentum_5min__m5_mine/setups_open10_blackout.html`.

It contains the 20 best and 20 worst completed round trips, with the exact S/R
level and tolerance zone observed by the state machine, entry/exit markers and
the best price reached during each trade. No Kronos forecast was requested or
used.

## 5. Validation and decision

- 49 focused engine/`sr_momentum` tests pass.
- 316 full-suite tests pass.
- The execution audit passes all five checks over 5,558 fills.
- `m5_test` was not touched.

**Decision:** keep the ten-minute prohibition because it is an explicit trading
constraint and now has safe semantics, but do not describe it as an alpha
improvement. On the available mining window it is economically unchanged.
The next useful change remains a time-of-day-aware cost model and a candidate
ranking/no-trade layer; further tuning of this gate on `m5_mine` would only add
another selected variant.
