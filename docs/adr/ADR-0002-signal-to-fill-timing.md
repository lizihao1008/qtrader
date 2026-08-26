# ADR-0002: Signals are decided at a bar's close and filled at the next bar's open

## Status

Accepted — 2026-08-26

## Context

The single most common way an intraday backtest becomes fiction is filling a
signal at a price the signal was derived from. Several conventions were
available: fill at the signal bar's close, fill at the signal bar's VWAP, fill
at the next bar's open, or model latency explicitly in wall-clock time.

The convention has to be enforced somewhere. If each strategy shifts its own
signals, some strategy eventually will not, and the resulting alpha will look
excellent.

## Decision

* A strategy's `target_position[t]` is defined as *decided using bars up to and
  including `t`*. Strategies never shift their own output.
* `BacktestEngine` applies `shift(execution_lag_bars)` centrally and fills at
  the `execution_price` column (default `open`) of the delayed bar.
* `execution_lag_bars` must be `>= 1`; `ExecutionConfig` raises otherwise, so
  same-bar execution cannot be configured even by accident.
* Position sizing uses equity marked at the previous bar's close — also known
  before the fill.
* The invariant is protected by a regression test that tampers with all bars
  after time `T` and asserts every fill at or before `T` is unchanged.

## Consequences

* Reported returns are conservative by roughly one bar of drift, which is the
  right direction for a research system.
* Strategy code stays simple: no shifting, no fill logic, no cost handling.
* Higher-frequency execution modelling (quote-driven entry, latency in
  milliseconds) will replace the bar-lag model in M5; until then, one bar is the
  stated resolution of the simulation.

## Alternatives Considered

* **Fill at the signal bar's close** — the signal is computed from that close;
  this is look-ahead unless the decision provably precedes the print.
* **Fill at the next bar's VWAP** — more realistic for larger orders, but VWAP
  over bar `t+1` is not known at its open; it needs an intra-bar fill model.
* **Explicit latency in seconds** — correct eventually, but meaningless at
  1-minute resolution and premature before quote data exists.
