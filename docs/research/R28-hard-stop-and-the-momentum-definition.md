# R28 — A hard stop, live tracking, and what `momentum_z` actually measures

**Date:** 2026-09-08
**Instructions:** (1) keep evaluating new bars after a position is open;
(2) hard stop at `min(150 bps, 2 ATR)`; (3) `momentum_z` turns positive far too
late — momentum should be computed from the last few bars.

**All three diagnoses were correct and all three are implemented. None of them
produces an edge, and the hard stop removes R25/R26's positive return — which
confirms R27: that return was the left tail, not an edge.**

## 1. `momentum_z` was not measuring momentum

`sr_momentum_5min.yaml` never sets `momentum_estimator`, so it defaults to
`session`:

```
z = sum(r since the open) / (sigma * sqrt(n))
```

The numerator is `log(close / open)`. **The statistic's sign is the sign of
`close − open`, so it cannot turn positive until price recovers the whole day's
move.** The module's own docstring says so — "a large early move fixes its sign
for the rest of the session however the price behaves afterwards" — but the
config was using it as the entry gate anyway.

AMD 2026-06-29, session open 522.83:

| bar | 10:15 | 10:30 | 11:00 | 11:15 | 12:00 | 12:45 | 13:00 | 13:15 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| close | 502 | 510 | 514 | 520 | 528 | 531 | 533 | 537 |
| close − open | −20.6 | −12.5 | −9.2 | −2.4 | +5.0 | +7.7 | +9.9 | +14.3 |
| **`z` session** | −1.95 | −1.20 | **−1.01** | −0.57 | −0.23 | −0.11 | +0.11 | +0.62 |
| **`z` ewma(6)** | −1.48 | **+0.49** | **+0.69** | **+1.04** | +1.21 | +0.28 | +0.74 | +2.45 |

The EWMA estimator turns positive at **10:30** and reads **+0.69 at 11:00** —
which is what the eye reads off the chart. The session estimator is still at
**−1.01** there, 13 points off the low. Across the session
`sign(z_session) == sign(close − open)` on **78% of bars**.

The EWMA estimator was already implemented; it simply was not selected.

## 2. The hard stop works, precisely

`hard_stop_bps` is applied last, after the sigma stop and the ATR cap, so it
binds whatever they produce and no volatility reading can lift it. With
`hard_stop_bps: 150` and `initial_stop_atr: 2.0` the effective initial stop is
`min(2·σ_H, 2·ATR, 150 bps)`.

| | worst single trade | trades < −200 bps |
| --- | --- | --- |
| original tight | −304 / −434 / −273 | 9 / 12 / 8 |
| **R25/R26 wide** | **−643 / −648 / −711** | **95 / 58 / 56** |
| **wide + hard stop** | **−237 / −232 / −245** | **6 / 7 / 4** |

(m5_mine / m5_validate / m5_test.) The AMD trade goes from −490 bps to −198.
This is better risk than the configuration that existed before R25.

## 3. Live tracking works

`track_breaks_while_held` lifts the `position == FLAT` gate in `_track_break`,
so the level machine keeps running under an open position. Setups seen rise
from 1,497 to 1,764 on `m5_test`. Paired with `exit_on_opposite_signal` the
strategy can now close a position when the other side fires.

## 4. And the returns get worse

| arm | `m5_mine` | `m5_validate` | `m5_test` | max drawdown |
| --- | ---: | ---: | ---: | --- |
| original tight | −7.95% | −4.23% | −1.36% | −12.7 / −19.5 / −7.7% |
| **R25/R26 wide** | −1.24% | **+1.64%** | **+1.47%** | −11.9 / −18.9 / −4.5% |
| wide + hard stop | −7.27% | −2.13% | −3.71% | −11.5 / −18.8 / −7.5% |
| + ewma momentum | −9.43% | −0.63% | −3.12% | −14.5 / −17.6 / −7.4% |
| + live tracking | −7.55% | −0.87% | −5.66% | −12.2 / −16.8 / −7.9% |
| tight + all three | −6.77% | −2.08% | −4.64% | −11.7 / −15.1 / −8.1% |

**The hard stop alone takes `m5_test` from +1.47% to −3.71%.** R27 predicted
exactly this: the wide exit's return *was* the fat left tail. Cap the tail and
the return goes with it. There is no version of that result which is both
positive and risk-controlled.

Note also that capping single-trade losses barely moves the **drawdown**
(−7.7% → −7.5% on `m5_test`): the drawdown is the accumulation of a negative
per-trade expectation, not a few disasters.

## 5. What to conclude

Three genuine defects were found by reading a chart, and all three are real:
a stop that no volatility reading should have been allowed to widen, a symbol
going blind while held, and an entry gate that measured distance-from-open and
called it momentum. Fixing them makes the strategy **more correct and less
profitable**, because what profit there was came from the defects.

The honest position after R21–R28: every mechanism in this strategy has now been
examined, and none of the candidate scores has measurable IC (R18 +0.0001,
R24 §1 `momentum_z` t = +2.65 / +0.04 across two windows). Correct machinery
around a signal with no information produces a correct machine that does not
make money.

## 6. Recorded

`hard_stop_bps` and `track_breaks_while_held` are implemented, tested and
default to off, so no existing result changes. The `ewma` estimator was already
present and remains non-default: it is the better *definition*, but selecting it
worsened `m5_mine` (−7.27% → −9.43%) and this project does not change defaults
on the strength of a preference.
