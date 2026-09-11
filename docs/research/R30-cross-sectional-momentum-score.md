# R30 — The cross-sectional momentum score, and whether it predicts

**Date:** 2026-09-08
**Deliverable:** an eight-factor intraday momentum score on a 1-minute grid,
cross-sectionally standardised, with a full entry/exit state machine — plus the
bucket analysis that says whether the score is worth trading.

> **Superseded in part by R31.** Four defects made this note's headline too
> strong: the short gate was unreachable (3,382 longs vs 84 shorts, so this was
> not the symmetric test it claims), trailing windows reached into the previous
> session, RVOL carried a signed weight, and the spread below is measured from
> the decision close — 58% of it accrues before a fill can reach it. The
> tradeable spread is -0.45 bps at 5 minutes, not -1.26. The direction of the
> finding survives; the magnitude and the symmetry claim do not.

**Result: the score has strong, clean, monotone predictive power, and it points
the wrong way.** Spearman between score bucket and forward return is **−0.988 at
5 minutes**, −0.818 at 15 and −0.855 at 30, over **118,826 observations per
bucket**. This is short-horizon cross-sectional mean reversion, measured on a
richer instrument than R04/R05 used and agreeing with them.

## 1. What was built

`strategies/xsec_momentum.py`. Eight factors per symbol per minute, each
z-scored across the eligible universe at that minute, then weighted into one
score which is itself ranked cross-sectionally:

`ret_1m`, `ret_5m`, `ret_15m`, `ret_30m`, `vwap_deviation` (distance from
session VWAP in units of the symbol's own bar volatility), `relative_ret_15m`
(against the sector ETF where the universe defines one, else the benchmark),
`rvol_5m`, `er_15m` (signed Kaufman efficiency ratio).

Entry needs the score to be extreme **and** the rank to be extreme; the two
disagree when the whole universe moves together, which is when a z-score alone
fires on all of it at once. Exits are the score fading through `exit_long` /
`exit_short`, an ATR stop, a 30-bar holding limit, or the session close.
A position that closes on a reversal does **not** open the other way on the same
bar — otherwise the exit and the entry are one decision and a single noisy bar
pays for both.

New shared features: `features/efficiency.py` (Kaufman ER, session-bounded),
`seasonality.seasonal_volume_ratio` (RVOL against the same minute of completed
prior sessions — a flat rolling average would call every open busy and every
lunchtime quiet, which is a clock), and `features/score.py` (weighted
combination that skips missing factors rather than scoring them as average).

## 2. Does the score predict? Yes — inverted

`m5_mine`, 120,424 bars × 22 symbols, deciles of the pooled score:

| bucket | score range | fwd 5m (bps) | hit | fwd 15m | fwd 30m |
| ---: | --- | ---: | ---: | ---: | ---: |
| 0 (lowest) | −2.92 … −0.77 | **+0.707** | 0.507 | +0.684 | +0.493 |
| 1 | −0.77 … −0.51 | +0.165 | 0.497 | −0.189 | −0.269 |
| 4 | −0.17 … −0.02 | −0.096 | 0.490 | −0.423 | −0.450 |
| 7 | +0.30 … +0.50 | −0.254 | 0.485 | −0.385 | −0.669 |
| 8 | +0.50 … +0.80 | −0.425 | 0.479 | −0.617 | −0.931 |
| 9 (highest) | +0.80 … +3.35 | **−0.555** | 0.477 | −0.539 | −0.678 |

| horizon | Spearman | top − bottom | n per bucket |
| ---: | ---: | ---: | ---: |
| 5 min | **−0.988** | −1.26 bps | 118,826 |
| 15 min | −0.818 | −1.22 bps | 118,826 |
| 30 min | −0.855 | −1.17 bps | 118,826 |

The hit rate declines monotonically too, 0.507 → 0.477. This is not a noisy
gradient with one odd bucket; it is an ordered ladder across all ten.

## 3. Every return factor is inverted; the volume factor is not

Spearman of each factor's own z-score against the forward return:

| factor | @5m | @15m | @30m | top − bottom @5m |
| --- | ---: | ---: | ---: | ---: |
| `z_ret_5m` | **−1.000** | −0.964 | −0.891 | −1.51 bps |
| `z_ret_1m` | −0.988 | −0.952 | −0.927 | −1.25 |
| `z_relative_ret_15m` | −0.988 | −0.782 | −0.648 | −0.94 |
| `z_ret_15m` | −0.867 | −0.418 | −0.564 | −0.89 |
| `z_er_15m` | −0.806 | −0.164 | −0.224 | −0.81 |
| `z_ret_30m` | −0.758 | −0.576 | −0.600 | −0.54 |
| `z_vwap_deviation` | −0.539 | −0.697 | −0.661 | −0.35 |
| **`z_rvol_5m`** | **+0.285** | +0.018 | −0.042 | +0.25 |

`ret_5m` is a *perfect* inversion at 5 minutes. The only factor not inverted is
the one that is not a return: relative volume, weakly positive and gone by 15
minutes. `er_15m` inherits the sign of the move it measures, so it inverts too.

## 4. It is not tradeable in this direction either

Inverting every return weight (keeping `rvol_5m` positive) confirms the sign and
does not produce money:

| arm | `m5_mine` trades | gross | net | return | hit |
| --- | ---: | ---: | ---: | ---: | ---: |
| as specified | 3,466 | −0.21 | −3.21 | −19.26% | 0.369 |
| **weights inverted** | 4,450 | −0.03 | −3.03 | −22.34% | **0.539** |

The hit rate jumps from 0.369 to **0.539** — the sign flip is real and it shows
up immediately. But gross per trade only reaches −0.03 bps, and the decile
spread that drives it is **1.26 bps against a 3.00 bps round trip**. The effect
is genuine, well-measured and roughly half the size of the toll.

## 5. What this says

The instrument works: eight factors, a clean cross-sectional pipeline, and a
bucket analysis that gave an unambiguous answer on 1.2M observations rather than
a P&L number that mixes the signal with the exits and the book.

The answer is that **intraday cross-sectional momentum on this universe is
reliably reversal, at every horizon from 1 to 30 minutes**, and the reversal is
too small to trade through a 3 bps round trip. That is the same conclusion
R04/R05 reached at rank IC −0.023, now with a monotone ladder behind it instead
of a single correlation.

## 6. Caveats

* `m5_validate` produced only 16–21 trades against `m5_mine`'s 3,466. That is a
  1-minute data coverage problem in that window, not a result — **check the
  store before reading anything from that split.**
* Equal weights are a starting point, not a search result. No weight was fitted.
* The score's own construction was not tuned: the bucket ladder is a property of
  the data, and tuning weights against it would be fitting to the answer.

## 7. Next

The honest next step is not to tune this score. It is to ask whether the
reversal is exploitable at a horizon where the spread exceeds the toll — the
decile spread does not grow between 5 and 30 minutes (1.26 → 1.17 bps), which
suggests it is not, and that measurement should be extended to 60 and 120
minutes before any more machinery is built.
