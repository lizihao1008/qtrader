# R31 — Audit of the cross-sectional momentum result

**Date:** 2026-09-08  
**Scope:** code and measurement review of `xsec_momentum`; no strategy change.

## Conclusion

The negative return does **not** support the broad claim that short-horizon
momentum does not exist. The current experiment asks a narrower question — do
recent relative winners among a small, changing IEX-eligible universe continue
to outperform recent relative losers? — and it contains material construction
and evaluation defects.

After separating those defects from the data, the useful result is narrower:
on `m5_mine`, this close-based one-minute cross-sectional price score mean
reverts over the next 5–15 minutes, but the executable part of that effect is
far below the assumed round-trip cost. Correcting the largest implementation
defect does not rescue the strategy.

## 1. Critical: the percentile gate nearly disables shorts

`cross_sectional_rank` uses pandas percentile ranks `rank(pct=True)`, whose
minimum is `1/N`, not zero. Entry requires:

```text
long:  rank > 0.95
short: rank < 0.05
```

The effective eligible universe is not 22 names. It has mean **9.87**, median
**9**, and reaches 21 or 22 names on only **1,091 of 120,424 bars**. Therefore
the short condition is mathematically impossible on almost every bar:
`1/N < 0.05` needs `N > 20`. The top name, by contrast, always has rank 1 and
can always pass the long gate.

Observed consequences:

| quantity | current result |
| --- | ---: |
| raw long entry candidates | 11,871 |
| raw short entry candidates | 206 |
| completed long trades | 3,382 |
| completed short trades | 84 |
| mean gross exposure | 5.2% |
| mean net exposure | **+5.0%** |

This is not the intended symmetric long/short cross-sectional test. Using a
rank mapped symmetrically to `[0, 1]`, `(ordinal_rank - 1)/(N - 1)`, changes the
book to 3,381 long and 2,427 short trades. It does **not** save the alpha: mean
gross falls from -0.27 to -0.45 bps/trade and net from -3.27 to -3.45. The bug
invalidates the original interpretation, but it is not the sole cause of the
loss.

## 2. Critical measurement mismatch: most quoted reversal arrives before fill

R30 labels a decision at bar `t` with `close[t+h]/close[t]`. The strategy sees
`close[t]` and can only fill at `open[t+1]`. On the entry-eligible clock, the
5-minute top-minus-bottom score spread decomposes as follows:

| interval | momentum top-minus-bottom |
| --- | ---: |
| decision close → 5m close | -1.35 bps |
| decision close → next open | **-0.75 bps** |
| next open → 5m close | -0.60 bps |
| next open → next open + 5m | -0.62 bps |

About **55%** of the headline reversal happens between the observed close and
the first executable reference price. On one-minute IEX last-sale bars this is
exactly where bid/ask bounce, sparse prints, and close/open discontinuities are
most likely to appear. It may be statistically real and still be unavailable
to the strategy.

After requiring a full 30 in-session bars of feature history, executable
open-to-open top-minus-bottom remains negative, but small: -0.71 bps at 5m,
-0.72 at 15m, and -0.42 at 30m, versus **3.00 bps** assumed round trip.

## 3. The score is not directionally symmetric

`rvol_5m` is an unsigned confirmation variable but is added to the directional
score with a positive weight. High volume therefore pushes every symbol toward
**long**, including a high-volume sell-off, and suppresses short entries. A
volume confirmation should gate or multiply the magnitude of a signed return
signal; it should not choose the side by addition.

More generally, momentum continuation is an interaction hypothesis:

```text
directional move AND efficient path AND unusual participation
```

The current weighted sum allows one large factor to compensate for a failed
confirmation. It tests substitutability, not confirmation. In a transparent
diagnostic, conditioning extreme 5-minute moves on above-average RVOL and
`|ER| >= 0.5` made executable continuation worse (-1.27 bps at 5m), so fixing
the composition is necessary for semantic correctness but is not evidence of
profitability.

## 4. Return windows cross the session boundary

`bar_log_returns` zeros the overnight gap, but `trailing_return` is a plain
rolling sum and does not restart by session. Consequently:

- `ret_30m` contains yesterday's closing returns until 10:00;
- `ret_15m` contains them until 09:45;
- `relative_ret_15m` has the same problem.

The strategy allows decisions from 09:35, so early entries use mixed-session
momentum. `seasonal_volume_ratio` also computes its recent five-bar numerator
with an ungrouped rolling mean, although this contamination has disappeared by
the configured first decision. Requiring 30 current-session bars does not flip
the empirical sign, but the features do not currently mean what their names
claim near the open.

## 5. Seven price inputs are mostly repeated measurements

The score gives equal votes to four overlapping returns, VWAP deviation,
relative 15-minute return, and signed ER. Their mean absolute pairwise
correlation is **0.45**; `ret_15m` correlates **0.91** with
`relative_ret_15m` and **0.87** with signed ER. This is not seven independent
confirmations. It is the same recent price shock counted several times, with
shorter and longer filters mixed together.

A cleaner design should use one directional residual-return horizon, then use
unsigned ER and RVOL as quality gates. Separate horizons should be evaluated as
separate hypotheses before they are combined.

There is one useful in-sample clue: enabling the already implemented
per-symbol volatility normalisation changes gross PnL from -0.27 to **+0.56
bps/trade**. With the symmetric rank correction it remains +0.51 bps/trade
(3,046 long / 2,063 short). This supports the concern that the raw score partly
ranks volatility and microstructure noise rather than risk-adjusted momentum.
It is not an edge: net is still -2.49 bps/trade before any out-of-sample check.

## 6. The data and statistical headline are overstated

- The nominal universe is 22 names but the liquidity mask admits a median of
  only 9 per minute. A 5% tail on nine names is not a stable percentile.
- The IEX feed is only a partial view of consolidated US volume and last-sale
  prices. That is especially weak for a one-minute volume/microstructure study.
- The 118,826 observations per decile are highly dependent: symbols share each
  minute and 5/15/30-minute labels overlap. Decile Spearman is descriptive, not
  a significance statistic. Using daily clusters, the executable top-minus-
  bottom spread has t=-3.88 at 5m, -2.24 at 15m, and only -0.72 at 30m.
- The bucket label is a raw stock return, although the project standard says a
  cross-sectional score should be assessed against cross-sectionally demeaned
  forward return. Top-minus-bottom spreads cancel much of the market component,
  but individual bucket means and hit rates do not.

## 7. This is not a test of market-wide time-series momentum

Cross-sectional z-scoring removes the common move. If all 22 stocks trend up,
the score still forces relative winners above zero and relative laggards below
zero. It cannot answer whether the broad market trend persists.

On the same data, an extreme trailing 5-minute SPY move has executable signed
forward return -0.15 bps at 5m, -0.06 at 15m, +0.10 at 30m and +0.73 at 60m.
The 60-minute sign is compatible with weak market-level persistence, but its
50.3% hit rate and sub-cost magnitude do not make it tradable. The 22-stock
pooled result remains negative through 30m and approximately zero at 60m.

Thus “short-horizon momentum may exist under some event/regime definition” and
“this unconditional one-minute cross-sectional score loses” can both be true.

## 8. Other correctness gaps

- The repository requirement to avoid the first ten minutes is not carried
  over: `no_entry_before: 09:35` permits fills from 09:36. There are 246 trades
  filled before 09:41 (7.1% of all trades).
- ATR stops are centred on the decision close, not the actual next-open fill.
  A gap changes the true risk distance.
- The strategy advances its internal holding state before knowing whether the
  engine obtained a fill. On a no-print execution bar, the engine may retry
  while the strategy already counts holding time and evaluates a stop.

## 9. Recommended next experiment

Do not optimise the current eight weights. First make the experiment identify
the intended effect:

1. Use a symmetric tail definition that works for variable `N`, and require a
   minimum cross-section large enough for ranking.
2. Reset every momentum/volume window at the session boundary and enforce the
   ten-minute blackout.
3. Measure labels from **next executable open** to future executable open (or a
   conservative VWAP proxy), cross-sectionally demeaned, with daily clustered
   uncertainty.
4. Separate market time-series momentum from stock-relative momentum.
5. Use one residual-return direction; make unsigned ER, RVOL, breadth and regime
   gates/interactions rather than additive directional votes.
6. Treat volatility normalisation as a pre-specified candidate, not a fitted
   answer; its +0.51 bps gross is economically too small but diagnoses what the
   raw score was ranking.
7. Report long and short arms separately, turnover, entry gap, and effective
   eligible-universe size by time of day.
8. Demand a gross edge comfortably above the cost estimate before adding an
   entry/exit state machine. A 0.6–0.7 bps executable spread cannot support a
   3 bps round trip.

The strongest current evidence is therefore not “momentum is impossible”. It
is: **this close-based, unconditional, one-minute relative-winner score mostly
measures a transient price shock, and neither its continuation nor its reversal
is large enough to trade after the next-open delay and costs.**
