# R01 — What separates a working trade from a failing one?

**Date:** 2026-08-26
**Strategy:** `cross_sectional_residual`, universe `us_liquid_22`, 1-minute IEX bars
**Windows:** `mine` 2026-02-02 → 2026-06-01 (82 sessions) · `validate` 2026-06-01 → 2026-07-28 (38 sessions)
**Artifacts:** `results/xsec_reversion__{split}[__override]/episodes/`

## 1. Question

The strategy loses money. Before adding complexity, three things had to be
established: what exactly it buys and sells, what a failure looks like, and
whether any observable condition separates the trades that work from the ones
that do not.

## 2. What the strategy actually does

At every rebalance bar (every 120 bars, anchored to the session open):

1. **residual** — for each stock, the log return left after removing its sector
   ETF's move scaled by a 120-bar trailing beta;
2. **signal** — that residual accumulated over the last 120 bars;
3. **score** — the signal z-scored *across the eligible cross-section at that
   bar*, then negated (reversion: a stock that lagged its sector scores high);
4. **entry** — long the top 3 scores above `+min_abs_zscore`, short the bottom 3
   below `-min_abs_zscore`, each side sharing half the gross budget;
5. **hold** — the book is frozen until the next rebalance;
6. **exit** — a position closes when the name drops out of the selection at a
   rebalance, or at 15:50 when everything is flattened.

There is no stop, no target and no exit on the signal itself. Every exit is a
clock event. That turns out to matter.

## 3. Method

`diagnose()` builds one **episode** per round trip: the K-line window from 60
bars before the entry to the exit, plus nineteen context features measured at
the **decision bar** (one bar before the fill, so nothing enters the analysis
that the strategy did not have), plus the outcome split into gross and net.
Fourteen of those features are universal market context; five are what this
strategy exposes about its own setup.

Screening nineteen features against one outcome guarantees false positives, so
|t| must clear the Bonferroni bar of **3.01** before a row is worth reading.
Hypotheses were generated on `mine` only; `validate` was touched once, after the
candidate was fixed.

## 4. What failure looks like

Mean episode path, oriented so "up" is profit for that trade's direction
(1,766 episodes, `mine`, uncapped):

| bars from entry | −60 | −30 | −10 | 0 | +10 | +30 | +60 | +120 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | +32.9 | +15.5 | +4.4 | 0.0 | −0.8 | −2.3 | −3.8 | −3.0 |
| winners | +30.9 | +13.8 | +3.3 | +2.0 | +12.9 | +28.9 | +47.2 | +69.4 |
| losers | +34.8 | +17.1 | +5.4 | −1.9 | −13.7 | −31.0 | −50.0 | −69.5 |

Three things stand out.

**The setup is real and correctly identified.** In the 60 bars before entry the
stock moves ~33 bps *against* the position the strategy is about to take. That
is exactly what a residual reversion rule should be picking up.

**Winners and losers have the same setup.** Their pre-entry paths are
indistinguishable — +30.9 vs +34.8 bps at −60 bars, converging to zero at the
entry. Whatever separates the two outcomes, it is not visible in the setup.

**After entry the outcome is a symmetric fan around zero.** The mean of all
episodes goes nowhere (−3 bps at +120 bars) while winners and losers spread to
±69 bps. The dislocation does not revert; the trade simply lands wherever the
noise happened to be at the clock-driven exit.

The excursions confirm it: 72% of losers were up more than 5 bps at some point
(mean MFE +26 bps before ending at −49), and 70% of winners were down more than
5 bps (mean MAE −25 before ending at +53). Realised PnL is a coin flip against
excursions three times its size.

**So there is no distinctive failure pattern — because there is no distinctive
success pattern either.** The honest one-line summary of "what does it look like
when it fails" is: identical to when it works.

## 5. Conditional screen

Nothing cleared the threshold on `mine`:

| feature | Spearman ρ | t | monotone bins? |
| --- | --- | --- | --- |
| `abs_score` | −0.053 | **−2.23** | no |
| `n_eligible` | −0.039 | −1.62 | yes |
| `vwap_distance_bps` | −0.037 | −1.56 | no |
| `minute_of_session` | +0.024 | +0.99 | no |
| everything else (15) | ≤ 0.02 | < 1.0 | no |

Time of day, market volatility, market trend, dispersion, relative volume,
stock volatility, beta, VWAP distance and bar range are all indistinguishable
from noise. The winners-vs-losers contrast agrees: the largest |t| there is 1.72.
The screen was rerun after the analysis was generalised to any strategy, which
added three universal features and raised the bar to 3.01; the ranking and the
conclusion did not change.

The one candidate, `abs_score`, points the *wrong way for the strategy* — the
highest-conviction trades are the worst:

| \|z\| bin | 0.00–1.14 | 1.14–1.34 | 1.34–1.58 | 1.58–1.93 | 1.93–4.36 |
| --- | --- | --- | --- | --- | --- |
| mean gross (bps) | −0.79 | +6.21 | +1.99 | −0.93 | **−7.21** |
| win rate | 47% | 53% | 49% | 47% | 44% |

Economically this is plausible: a residual move far enough out of line is more
likely to be information than noise, and betting on it reverting is betting
against news.

## 6. Confirmation on `validate` — partly negative

**The episode-level gradient did not replicate.** On `validate` the `abs_score`
bins are +2.4 / −11.0 / +7.0 / −10.3 / +1.9 — no gradient, and the top bin is
positive. As a per-trade conditioning variable, `abs_score` failed.

**The backtest-level effect partly did.** Capping conviction changes which names
get selected, and across a sweep of caps both windows agree that *some* cap
beats none:

| `max_abs_zscore` | 1.3 | 1.4 | 1.5 | 1.58 | 1.7 | 1.9 | 2.2 | none |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mine` net | −0.7% | +0.4% | +1.5% | **+3.2%** | +2.8% | −2.1% | −4.4% | −8.3% |
| `validate` net | −0.0% | −0.2% | −1.1% | −0.1% | −4.2% | −4.9% | −4.8% | −6.5% |

On `mine` the surface is a smooth hump. On `validate` it is not: 1.5 is worse
than both its neighbours, and the apparent 1.58 result sits between −1.1% and
−4.2%. **The level does not replicate; only the ordering does** — uncapped is
the worst row in both windows, by a wide margin.

Even at its best, `validate` with a cap is break-even: gross +$3,774 against
$3,826 of costs. The edge pays the spread and nothing more.

### 6b. And it stays negative with the cap in place

Rerunning the screen on the shipped (capped) configuration finds nothing either
— `mine` strongest `vwap_distance_bps` at t = −1.62, `validate` strongest
`move_in_vols` at t = +1.72, against a bar of 3.01. Whatever the cap does, it is
not creating a conditional structure that the screen can see.

## 7. Decision

`max_abs_zscore: 1.5` is now set in `config/backtest/xsec_reversion.yaml`.
The value is the round midpoint of the region where both windows agree, chosen
deliberately **away** from the in-sample optimum of 1.58. It is recorded as a
band-limited hypothesis, not validated alpha: it converts a losing strategy into
a break-even one, which is a smaller claim than it may look.

`validate` is now spent. It cannot be used again for this strategy.

## 8. What this says about the next step

The diagnosis is not "the signal needs a better filter". It is:

1. **The reversion hypothesis is weak-to-absent at this horizon on this data.**
   The mean post-entry path is flat. No conditioning variable rescues it.
2. **The exit is the least defensible part of the design.** Every exit is a
   clock event, and MFE/MAE are ~1.5x the realised return. A signal-based or
   volatility-scaled exit is a change worth testing on its own merits — it is
   motivated by the mechanism, not by a p-value.
3. **The strategy re-affirms losers.** A name stays selected precisely while its
   residual keeps diverging, so a losing position is renewed at each rebalance.
   Trades held beyond 120 bars average −88 bps at an 11% win rate. Part of that
   is survivorship — the trade is open *because* it is losing — but the absence
   of any maximum holding period is a design gap regardless.
4. **The cost floor is the binding constraint.** At ~3 bps per round trip and a
   gross edge of the same order, no amount of signal refinement at this turnover
   will produce net alpha. Either the horizon lengthens materially or the feed
   improves (IEX carries a small share of consolidated volume, and this is a
   microstructure-adjacent claim being tested on a thin tape).

Items 2 and 3 are design changes justified by mechanism. Item 1 says a
supervised model (M2) trained on the same features would most likely learn the
same nothing — the useful M2 experiment is one that changes the *target*
(longer horizon, triple-barrier) rather than the model class.
