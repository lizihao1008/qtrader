# ADR-0001: Build the first vertical slice single-symbol, and add two modules the outline does not list

## Status

Accepted — 2026-08-26

## Context

The outline (§15) specifies the eventual package layout: `data/`, `universe/`,
`features/`, `regime/`, `labels/`, `models/`, `backtest/`, `risk/`,
`execution/`, `experiments/`, `utils/`. The project thesis is cross-sectional:
peer-relative alpha ranked across a universe.

But the first functional target in `CLAUDE.md` §26 is narrower — bars →
validated dataset → deterministic features → cost-aware backtest — and the
immediate requirement was to see a strategy's entries and exits on a K-line
chart with a return-vs-time curve after the test.

Building the cross-sectional machinery first would mean writing the universe,
peer and ranking layers before anything could be validated end to end.

## Decision

1. **The first slice is single-symbol.** `BacktestEngine` simulates one symbol
   with one position. Cross-sectional selection is deferred to M1/M2, when the
   `universe/` layer exists.
2. **Add `strategies/`**, not listed in the outline. Deterministic V0 rules
   (`models/alpha/` in the outline) are not fitted models; keeping them in their
   own package makes the "target position per bar" contract explicit and keeps
   `models/` free for trained estimators.
3. **Add `viz/`**, not listed in the outline. Charting is a reusable
   deliverable, not notebook scratch: the report is written by production code
   and archived with each run, per the notebook policy (§18).

## Consequences

* An end-to-end reproducible loop exists now, so every later layer is added
  against a working baseline instead of in the dark.
* `BacktestEngine` will need extending to hold several positions at once. Its
  timing contract (signal at close `t`, fill at open `t+1`) is designed to
  survive that change; the portfolio and sizing code is what will grow.
* `strategies/` and `models/` must not blur: anything with fitted parameters
  belongs in `models/`.

## Alternatives Considered

* **Cross-sectional engine first** — matches the thesis, but nothing would be
  runnable or verifiable for much longer, and the leakage/cost invariants would
  be established on untested code.
* **Charts in notebooks** — fastest, but violates §18 and would leave every run
  unarchivable.
* **Strategies under `models/alpha/`** — closer to the outline, but conflates
  deterministic rules with trained models and hides the position contract.
