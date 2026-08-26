# ADR-0003: Strategies emit a weight vector, and the single-symbol engine is deleted

## Status

Accepted — 2026-08-26. Supersedes the single-symbol scope decision in
[ADR-0001](ADR-0001-first-vertical-slice-layout.md) §1.

## Context

The M0 slice defined a strategy as producing `target_position: Series` in
`{-1, 0, +1}` for one symbol, and the engine held one position. M1 needs the
cross-sectional case the project was designed for: rank a universe at each
timestamp and hold several names long and short simultaneously.

Two shapes were possible. Keep the single-symbol engine and add a portfolio
engine beside it, or generalise the one engine so a single symbol is the
degenerate case. CLAUDE.md §9 forbids parallel implementations kept "for
safety", and ADR-0001 already recorded that the timing contract was designed to
survive this change.

The unit of the interface also had to change. `{-1, 0, +1}` cannot express "a
quarter of the book in this name", and pushing sizing into strategies would put
capital allocation in the same place as alpha.

## Decision

* `StrategySignals.target_weights` is a `timestamp x symbol` frame of **signed
  fractions of deployed capital**, constrained to `sum(|w|) <= 1` per row. A row
  of zeros is an explicit no-trade state.
* Strategies receive a `MarketContext` (aligned panel, universe, tradability
  mask) instead of a bare bar frame, so every strategy sees the same definition
  of what was tradable at each bar.
* `ExecutionConfig.position_fraction` becomes `gross_leverage`: the engine
  converts weights to notionals with `equity * gross_leverage * w`.
* The single-symbol engine, portfolio and `Strategy` interface are **deleted**,
  not deprecated. `MACrossStrategy` was migrated to the new interface and now
  splits capital evenly across whatever universe it is given; on a one-symbol
  universe it reproduces the original baseline.
* The anti-churn rule generalises: the engine trades a symbol when its target
  *weight* changes, not when its share count drifts.

## Consequences

* One engine, one timing contract, one set of cost and accounting rules for
  every strategy — a bug fixed there is fixed everywhere.
* Run configs changed shape (`universe:` replaces `data.symbol:`), and every
  existing config had to be migrated. That is a one-time cost paid at the point
  where there were two configs rather than fifty.
* The engine now needs a tradability mask, which makes the universe layer a
  hard dependency of every run rather than an optional extra. This is the
  intended direction: "which symbols existed and could be traded at time t" is
  not a detail a backtest may skip.
* Whole-share rounding matters more: a weight of 0.17 in a $600 stock rounds
  differently than in a $30 one. Acceptable at current capital; revisit if the
  universe widens to expensive names with small books.

## Alternatives Considered

* **Two engines side by side** — no migration risk, but two definitions of
  fills, costs and timing, and CLAUDE.md §9 explicitly rules it out.
* **Keep `{-1, 0, +1}` and size in the engine by position count** — simpler,
  but it cannot express unequal conviction and would have to be replaced again
  the moment position sizing becomes a research question.
* **Long-format `(timestamp, symbol)` MultiIndex instead of wide frames** —
  more memory-efficient for sparse universes, but cross-sectional ranking and
  z-scoring read naturally as row-wise operations on wide frames, and clarity
  wins at this universe size.
