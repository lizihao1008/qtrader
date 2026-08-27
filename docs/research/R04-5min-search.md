# R04 — Migrating to 5-minute bars, and a systematic search for positive expectancy

**Started:** 2026-08-26
**Data:** 5-minute IEX bars, 2024-01-02 → 2026-08-26, 664 sessions, 1.56M bars
**Splits (fixed before any 5-minute result was seen):**
`m5_mine` 2024-01 → 2025-04 · `m5_validate` 2025-04 → 2026-01 · `m5_test` 2026-01 → 2026-08

Everything below is on `m5_mine` unless stated. `m5_validate` has not been used.

## 1. The timeframe audit

Every parameter measured in *bars* was rescaled to span the same wall-clock time;
everything per-fill, per-order or dimensionless was deliberately left alone. The
table is in `config/backtest/trend_ratchet_5min.yaml`. Two points worth keeping:

* the risk unit is **timeframe-invariant** by construction. `sigma_H = sigma_bar
  * sqrt(horizon_bars)`, and since `sigma_5min ≈ sigma_1min * sqrt(5)` while
  `horizon_bars` falls from 60 to 12, `sqrt(12) * sqrt(5) = sqrt(60)`. A
  "1-sigma stop" means the same distance at either resolution.
* `execution_lag_bars: 1` is now a **5-minute** delay rather than a 1-minute
  one — strictly more conservative, not less.

## 2. Two real bugs, both found by auditing rather than by reading code

**Fills were possible on bars where nothing traded.** The engine gated new
exposure on the *liquidity mask at the decision bar* while its docstring claimed
it gated on "did the symbol print in the execution bar". Those are different:
the liquidity mask tolerates `max_stale_bars` of silence. 85 fills in one window
landed on bars with no print, of which only 54 were declared stale exits. The
engine now checks `panel.traded` at the execution bar — a property of the bar the
order would have rested in — *and* keeps the eligibility check as a separate
guard.

**The pipeline stored in-progress bars.** A bar timestamped `t` covers
`[t, t+interval)` and is only a fact once that interval has closed. Fetched
during market hours, the last bar of every download was partial: AAPL's 18:40
5-minute bar was stored with close 313.275 on volume 3,264; the settled bar was
313.685 on 7,018. This is the mirror image of look-ahead — a bar that never
existed — and it is equally capable of inventing a signal. Found because the raw
layer's immutability guard fired on a re-download, which is exactly what that
guard is for. `drop_incomplete_bars` now discards them at ingestion, and the
affected sessions were dropped and re-ingested.

The full execution audit (`scripts/audit_execution.py`) now passes on the real
5-minute data: fills at the correct bar's open, prices inside each bar's traded
range, spread always paid adversely, no unexplained fills on silent bars, and
tripling every bar after a cut date leaves all 2,387 earlier fills identical.

## 3. The trend premise is dead, and the data says so directly

Rank IC of each candidate signal against forward returns, m5_mine:

| signal | h=1 | h=3 | h=6 | h=12 |
| --- | --- | --- | --- | --- |
| trend z (session drift) | −0.0047 (t −2.6) | −0.0073 (−3.7) | −0.0082 (−4.1) | **−0.0101 (−4.7)** |
| reversal, 1 bar | +0.0411 (+23.0) | +0.0303 | +0.0238 | +0.0210 |
| residual reversal, 3 bars | +0.0349 (+21.5) | +0.0274 | +0.0232 | +0.0212 |
| residual reversal, 12 bars | +0.0201 (+12.4) | +0.0200 | +0.0217 | **+0.0232 (+12.8)** |
| −VWAP distance | +0.0127 | +0.0154 | +0.0175 | +0.0210 |
| relative volume | ~0 | ~0 | ~0 | ~0 |

Trend following is **reliably wrong** on this universe at this horizon. Reversal
is reliably right. The 5-minute baseline confirmed it: `trend_ratchet` returned
−8.83% with gross −1.03 bps per trade (t = −0.71).

## 4. Most of the reversal IC is untradable

Repeating the IC with an implementation lag — signal at bar `t`, entry at
`t + lag` — separates information from microstructure:

| signal | lag 0 | lag 1 | lag 2 | lag 3 |
| --- | --- | --- | --- | --- |
| reversal 1 bar (h=1) | +0.0411 | **+0.0034** | −0.0002 | +0.0015 |
| residual reversal 12 bars (h=12) | +0.0232 | **+0.0170** | +0.0168 | +0.0157 |

The one-bar reversal loses **92% of its IC** to a single bar of delay: it is
bid-ask bounce, and bounce is not tradable — capturing it means buying at the
ask and selling at the bid, which is the spread. The 12-bar residual reversal
keeps 73% and is flat across lags 1–3, so it is not a bounce artifact.

## 5. The edge is real, and it is about half the cost

Non-overlapping windows (one observation per holding period, not per bar —
overlapping samples inflated the t-statistics by up to 8x):

| | edge | t |
| --- | --- | --- |
| top-3 minus bottom-3 of 22, 12-bar hold | +4.08 bps | +2.84 |
| long leg alone (beaten-down decile) | +0.85 bps | +0.86 |
| **short leg alone (stretched decile)** | **+3.77 bps** | **+2.91** |

The edge is **one-sided**: shorting what has run up carries it, buying what has
fallen contributes nothing. That halves the cost, since a one-legged book pays
one leg.

**Cost calibration.** Roll's estimator on 5-minute bars gives a median implied
half-spread of 1.85 bps — but that is inflated by genuine 5-minute mean
reversion, which is the very effect being traded. On 1-minute bars, which
isolate the bounce, the median is **0.68 bps**. So a realistic round trip is
~2.4 bps against the 3.0 bps the config assumes: the cost model is conservative,
not optimistic. (An earlier draft of this note said the opposite, on the
5-minute estimate alone.)

## 6. The renewal trap, and the fix

The backtest kept returning gross ≈ 0 while the score measurably carried an
edge. Reconciling them against the executed trades:

| holding | trades | gross |
| --- | --- | --- |
| ≤ 6 bars | 875 | +0.34 bps |
| **exactly 12 bars** | 3,197 | **+11.95 (t +10.0)** |
| 13–24 bars | 515 | **−54.59 (t −15.8)** |
| > 24 bars | 100 | **−117.35 (t −10.4)** |

A position is held past the first rebalance exactly when the name is *still* at
the extreme — that is, when the price kept moving against the reversion bet. The
strategy was renewing its losers.

Both figures above are conditioned on the outcome and neither can be earned as
stated, so the fix has to be justified by the mechanism: a reversion signal is a
claim with a deadline — *this has moved too far and should come back within the
interval*. If it has not, the claim has been falsified, and renewing the position
doubles down on a rejected hypothesis rather than acting on a fresh one.
`max_hold_intervals` retires the position instead.

Measured end to end, it does what the mechanism predicts and nothing more:
gross per trade **−0.40 → +1.69 bps** (t −0.40 → +1.78), matching the +1.67 bps
the score analysis predicted in advance.

## 7. Where the search stands

Trials on `m5_mine` (full ledger: `results/search/ledger.jsonl`):

| # | trial | gross/trade | t | net/trade | verdict |
| --- | --- | --- | --- | --- | --- |
| 1 | trend_ratchet, 5-min | −1.03 | −0.71 | −4.03 | signal |
| 2 | xsec residual reversal, long/short | −0.43 | −0.59 | −3.43 | signal |
| 3 | short-only, 22 names | −0.40 | −0.40 | −3.40 | signal |
| 4 | short-only + max_hold_intervals=1 | **+1.69** | **+1.78** | −1.31 | signal |
| 5 | tighter selection (2 of 22) | +1.22 | +1.12 | −1.78 | signal |
| 6 | longer hold (24 bars) | +0.83 | +0.55 | −2.17 | signal |

Best so far: **+1.69 bps of gross against 2.4–3.0 bps of cost.** The edge is
real and the sign is stable, but it is roughly two-thirds of what it costs to
harvest. Nothing yet clears.

Ruled out along the way, each with a measurement rather than an opinion:
trading only the cheap-spread names (edge does not live there), conditioning on
cross-sectional dispersion (no gradient), conditioning on time of day (no usable
concentration), longer holding periods (edge does not accumulate), tighter
selection (worse).

## 8. Robustness of the best candidate — it does not hold up

Before spending `m5_validate` on it, the candidate (short-only, 3 of 22,
`max_hold_intervals=1`, lookback 12, rebalance 12) was checked for the two things
that decide whether a result is worth confirming.

**Parameter sensitivity — the chosen point is a peak, not a plateau:**

| lookback | rebalance | n | gross |
| --- | --- | --- | --- |
| 6 | 12 | 3 | +0.28 |
| 12 | 6 | 3 | +0.59 |
| **12** | **12** | **3** | **+1.69** |
| 12 | 12 | 4 | +1.36 |
| 12 | 18 | 3 | +0.69 |
| 18 | 12 | 3 | +1.31 |
| 24 | 12 | 3 | +1.15 |

Nothing else exceeds +1.4 and the nearest neighbours fall to +0.28 and +0.59.
The parameters were chosen from the IC scan *before* any of these backtests, so
this is not pure selection — but a sharp local maximum is what an overfit looks
like, and +1.69 should be read as the top of a range whose centre is nearer
+1.1.

**Cost sensitivity — it needs execution cheaper than reality:**

| per-fill cost | 1.50 (config) | 1.18 (realistic) | 0.84 | 0.59 | 0.00 |
| --- | --- | --- | --- | --- | --- |
| net per trade | −1.31 | −0.67 | **0.00** | +0.51 | +1.69 |

Breakeven is 0.84 bps per fill. The Roll(1-minute) implied half-spread *alone*
is 0.68 bps, leaving 0.16 bps for slippage on a marketable order. The strategy
needs execution roughly **1.4x cheaper than a realistic estimate** merely to
break even.

**Sub-period stability — driven by two quarters:**

| | 2024Q1 | 2024Q2 | 2024Q3 | 2024Q4 | 2025Q1 |
| --- | --- | --- | --- | --- | --- |
| gross | −2.16 | +3.25 | +4.72 | +1.36 | +0.99 |
| t | −1.18 | +1.66 | **+2.16** | +0.66 | +0.40 |
| net @1.18 | −4.52 | +0.89 | **+2.36** | −1.00 | −1.37 |

Four of five quarters are positive gross, but only two are net positive and only
one is individually significant. The book also runs at **−75% net exposure** —
short-only carries an uncompensated beta that happened to be survivable over a
rising 2024 and would not be in a sharp rally.

Two further mechanism-motivated tests, both rejected: removing the z floor
(+1.39) and capping conviction as R01 suggested (+1.00) each made it worse.

## 9. Breadth — the last lever, and it goes the wrong way

The hypothesis was that the extremes of a 100-name cross-section are more
extreme than those of 22, so the same three positions would sit at the 3rd
percentile instead of the 13th. Short-leg edge at matched percentiles:

| percentile | 22 names | 106 names |
| --- | --- | --- |
| bottom 2% | — | +1.72 (t +0.91) |
| bottom 5% | **+5.76 (t +2.48)** | +1.52 (t +1.71) |
| bottom 10% | +3.77 (t +2.91) | +0.92 (t +1.72) |
| bottom 20% | +2.12 (t +2.76) | +0.61 (t +1.82) |

**Breadth makes it worse at every comparable percentile.** The likely reason is
that the 22-name list is all mega-caps with tight spreads and heavy IEX
coverage, while the wider list reaches into names where an IEX-derived signal is
noisier — the opposite of the assumed mechanism. Either way the lever fails.

## 10. Confirmation, and the conclusion

The candidate was settled after eight trials on `m5_mine` and taken **once** to
the held-out window, with no tuning:

| | m5_mine (15 months) | m5_validate (9 months) |
| --- | --- | --- |
| trades | 4,565 | 2,864 |
| gross per trade | **+1.69 bps** | **+0.23 bps** |
| t | +1.78 | **+0.19** |
| expectancy net | −1.31 bps | −2.77 bps |
| total return | −24.9% | −30.3% |
| Sharpe | −1.17 | −2.44 |

**The edge does not replicate.** In-sample it was already below the
multiple-testing bar for eight trials; out of sample it is +0.23 bps at
t = +0.19 — indistinguishable from zero. And even had it replicated at +1.69 it
would still have been roughly two-thirds of what it costs to harvest.

### Verdict

**On this universe, at these horizons, with IEX data and realistic costs, the
current design has no reliable positive expectancy.** That is a conclusion, not
a pause: it rests on nine backtests, ~200 measured cells, an out-of-sample
confirmation, and a cost model calibrated from the data rather than assumed.

`m5_test` (2026-01 → 2026-08) was **never run**. There was no candidate worth
spending it on, and it stays clean for whatever comes next.

### What was established along the way

Negative results that are worth as much as a positive one would have been:

1. **Trend following is reliably wrong here** — IC −0.010 at t = −4.7. Not
   "unprofitable"; systematically the wrong sign. Any future work on this
   universe should start from reversal.
2. **Most of the reversal signal is untradable.** The headline IC of +0.041 is
   92% bid-ask bounce and dies at one bar of implementation lag. Anyone reading
   that number without the lag test would build a strategy on an artifact.
3. **What survives is one-sided and small.** Shorting the stretched decile
   carries essentially all of it; the long leg is worth +0.85 bps at t = +0.86.
4. **The renewal trap costs more than the signal is worth.** Renewing a position
   because it is still extreme means renewing exactly the trades whose thesis
   has been falsified. Fixing it moved gross by +2.1 bps — larger than the
   entire remaining edge.
5. **Cost is the binding constraint, and it is not conservative.** Roll on
   1-minute bars implies a 0.68 bps half-spread. Breakeven for the best
   candidate needs 0.84 bps per fill *including* slippage.

### What would have to change to make this worth revisiting

Not more parameters. The three things that would move the arithmetic:

* **A better data source.** IEX carries a small share of the tape, and every
  volume- and microstructure-adjacent measurement here inherits that. SIP data
  would make the same tests mean something different.
* **A longer horizon.** Everything tested lives inside a session and pays ~2.4
  bps per round trip. A daily-horizon version pays the same per trade over
  twenty times the holding period. The overnight-crossing cells in §5 hinted at
  larger effects; they were not significant once overlap was removed, but they
  were never properly tested with daily data.
* **A different question.** Every signal tried was a function of past returns.
  The measurement framework built here — IC by implementation lag, non-overlapping
  significance, cost calibrated from the data, attribution to signal/holding/
  frequency/cost — is signal-agnostic, and would give a straight answer about a
  genuinely different input.
