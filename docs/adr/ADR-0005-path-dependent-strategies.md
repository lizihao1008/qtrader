# ADR-0005: Path-dependent exits are a bar loop inside the strategy, not a new engine

## Status

Accepted — 2026-08-26

## Context

Every strategy so far mapped market state to weights with vectorised pandas
operations: the weight at bar `t` was a function of the data at bar `t`.

`trend_ratchet` is not like that. Its stop depends on the highest close *since
its own entry*, so the weight at bar `t` depends on when the position opened,
which depends on earlier weights. This is genuinely path-dependent — it cannot
be written as a transform of the inputs, no matter how clever the rolling
window.

Trailing stops, maximum holding periods, breakeven ratchets, scaling in and out,
and cooldowns after a loss all have this shape, so the question is not specific
to one strategy: **where does per-position state live?**

Three options were available. Push the exit logic into the backtest engine as a
first-class stop-order concept. Introduce a second, event-driven strategy
interface alongside the vectorised one. Or keep the single interface and let a
strategy run its own loop.

## Decision

Keep the existing interface. `Strategy.generate(context) -> StrategySignals` is
unchanged; a path-dependent strategy runs an explicit loop over bars inside it,
vectorised across symbols with numpy, and returns the same weight frame as any
other strategy.

The engine learns nothing about stops.

## Consequences

* **One strategy contract and one engine.** A path-dependent strategy is
  simulated by exactly the same fill, cost and timing code as a vectorised one,
  so its results are comparable and there is no second place for a timing bug to
  hide.
* **Exit policy stays research, not infrastructure.** A stop is a modelling
  choice with parameters to test, and it belongs where the hypothesis lives. Had
  it gone into the engine, changing it would mean changing the simulator that
  every other result depends on.
* **The lag applies to exits too.** A stop breach detected at bar `t`'s close is
  filled at `t+1`'s open, because the strategy expresses it as a weight like
  anything else. This is more conservative than an intrabar stop fill and is the
  honest model given the engine only fills at bar prices — but it must be
  documented per strategy, because a reader expects stops to be exact.
* **Cost.** The loop is Python: ~32k bars x 22 symbols runs in about two
  seconds. Acceptable now; a strategy needing tick-level state would force this
  decision to be revisited.
* Strategies that do not need state pay nothing — they keep their vectorised
  implementation.

## Alternatives Considered

* **Stops as engine-level orders.** The realistic long-term design for live
  trading, where a stop really is an order resting at the broker. Rejected for
  now: it would put a research parameter inside production simulation code, and
  the intrabar fill question it raises is exactly the one this project should
  not answer optimistically. Revisit at M5/M6, when order lifecycle is modelled
  for real.
* **A separate event-driven interface.** Two interfaces means two definitions of
  the timing contract, and CLAUDE.md §9 rules out parallel implementations kept
  for convenience.
* **Approximating the trailing stop with rolling windows.** A rolling maximum is
  not the maximum since entry, and the difference is largest exactly where it
  matters — a long-held winner. It would have been a quietly wrong backtest.
