# Current Task

## Goal

M1: extend the single-symbol slice into a cross-sectional pipeline and add the
project's first real alpha rule — peer-relative residual ranking across a
universe, evaluated with costs and with signal-quality diagnostics.

## Current State

Complete. The pipeline runs a 22-name universe end to end and both strategies
work. 84 unit + integration tests pass.

**The cross-sectional strategy does not survive out-of-sample testing.** The
in-sample result was a small-sample artifact. Details below.

## Completed

- `data/panel.py` — `BarPanel`: symbols aligned on one grid, session-bounded
  forward fill, `traded` / `available` masks.
- `universe/` — `Universe` (symbols, benchmark, sector map) and
  `LiquidityFilter` (price, trailing dollar volume, staleness), all per-bar.
- `features/relative.py` + `features/ranks.py` — overnight-gap-free log returns,
  reference alignment, rolling beta, residual returns, cross-sectional
  rank/z-score over eligible symbols only.
- `strategies/cross_sectional.py` — the new strategy (see ADR-0003 for the
  interface change that made it possible).
- Portfolio engine: `target_weights` replaces `target_position`; multi-symbol
  `Portfolio`; fail-closed on symbols that did not print; `gross_leverage`.
- `backtest/signal_metrics.py` — forward returns and rank IC by horizon.
- `runner.py` — one definition of the pipeline, shared by scripts and tests.
- `experiments/sweep.py` + `scripts/sweep.py` — parameter sensitivity.
- Charts: exposure chart, score panel, candle cap and line bucketing so
  multi-month reports stay openable (29 MB → 5.7 MB).
- `ma_cross` migrated to the new interface; the old single-symbol engine,
  portfolio and interface were deleted.

## Findings

Universe `us_liquid_22`, 1-minute IEX bars, sector-residual reversion,
3 positions per side, 1.5 bps impact per fill.

### Costs dominate at high turnover

The first configuration (lookback 30, rebalance every 15 bars) returned
**−6.0%** over 21 sessions: gross **+$4,157** against **$10,155** of costs, at
33x daily turnover. Cost per round trip ($4.97) matched
`notional x 1.5 bps x 2 legs` to the cent, so the model is right and the
strategy simply traded too much.

### In-sample the signal looked real

Sweeping lookback x rebalance interval produced a smooth, plausible surface —
not a single lucky cell:

| | rebalance 60 | 120 | 240 |
| --- | --- | --- | --- |
| lookback 30 | −2.68% | −0.45% | −2.50% |
| lookback 60 | +0.44% | +2.15% | +1.23% |
| lookback 120 | **+4.50%** | +4.16% | +3.98% |

At lookback 120 / rebalance 120: **+4.16%** net over 21 sessions, Sharpe 4.4,
max drawdown −2.5%, turnover 7.3x, rank IC **+0.013 / +0.022 / +0.029** at
5/15/30 bars (t = 4.6 / 7.6 / 9.8). Reversion and momentum were exact mirrors,
with reversion positive at every lookback — a consistent sign, not noise.

### Out of sample it disappears

Same parameters, the five and a half months *before* that window
(2026-02-02 → 2026-07-28, 121 sessions, 47,190 bars):

| | in-sample (21 sessions) | out-of-sample (121 sessions) |
| --- | --- | --- |
| net return | **+4.16%** | **−14.86%** |
| gross PnL | +$6,556 | −$2,309 |
| rank IC 5b / 15b / 30b | +0.013 / +0.022 / +0.029 | +0.002 / −0.001 / −0.002 |

The IC does not replicate and is sign-unstable; gross PnL is negative before
costs. Data coverage is not the explanation — 21.4 eligible names per bar on
average, 99.9% of bars with sufficient breadth. **The in-sample edge was an
artifact of a 21-session window.**

This is the correct outcome of a first research loop, and it is why the
out-of-sample step exists. The framework caught it in one run.

## Open Issues

- The one-month in-sample window was far too short to conclude anything from.
  Future work should start from the multi-month dataset, with a chronological
  train/validation/test split defined *before* looking at results.
- IEX-only bars are a thin proxy for the consolidated tape. A genuine intraday
  cross-sectional effect may simply not be visible in this feed; that is a data
  question, not a modelling one, and it caps what M2 can prove.
- Shorts carry no borrow cost or locate model, and the strategy is ~50% short.
- Universe membership is static — fine for these windows, wrong for a
  multi-year study.
- No walk-forward evaluation yet; the sweep is in-sample by construction.

## Next Actions

1. Define a chronological train/validation/test split over the full
   Feb–Aug dataset and hold the test window untouched.
2. Add walk-forward evaluation to `experiments/` so a result is scored across
   rolling windows instead of one period.
3. Test whether a longer holding horizon (multi-hour to daily) shows a residual
   effect that survives costs, before adding model complexity.
4. Only then M2: LightGBM cross-sectional ranker, compared against this
   deterministic baseline on identical splits.
5. Add borrow-cost modelling before any short-heavy result is taken seriously.

## Files Touched

`config/universe/*`, `config/backtest/*`, `scripts/*`, `src/qtrader/**`,
`tests/**`, `docs/**`, `README.md`.

## Validation

- `pytest` — 84 passed.
- Leakage guards: tampering with bars after time T leaves fills, features and
  the tradability mask before T unchanged.
- Accounting guard: `sum(trades.net_pnl)` equals the equity change on a
  flat-ending run.
- Cost-model check: measured cost per round trip matches the analytic value.
- Regression: `ma_cross` on PLTR still runs on the new architecture
  (+6.41% net, 96 trades) and matches the pre-refactor result up to the wider
  union index.
- Reports for all three runs rendered and inspected in a browser.
