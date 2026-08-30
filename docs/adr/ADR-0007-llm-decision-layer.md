# ADR-0007: LLM as a validation layer between strategy and engine

## Status
Accepted

## Context

The request is to add a local-LLM second opinion to the existing intraday
system, producing:

    Quant Strategy → Candidate Signal → LLM Context Analysis → Final Decision → Risk / Execution

with the LLM as a filter, never as an unconstrained order source, and with both
modes runnable over identical data for a fair comparison.

### What the existing code already fixes

* `Strategy.generate(context) -> StrategySignals` returns `target_weights` for
  the **whole history at once**; the engine applies one global
  `shift(execution_lag_bars)`. There is no per-bar callback to hook.
* `BacktestEngine.run` is the only place fills happen, and it already refuses to
  open exposure unless the execution bar printed *and* the symbol was eligible
  at the decision bar.
* **There is no `risk/` module.** Risk today is three separate things: the
  liquidity mask on `MarketContext.tradable`, strategy-internal limits
  (`max_positions`, `max_weight`, `flat_time`), and the engine guards above.
* `StrategySignals.indicators` already carries a per-symbol frame of exactly the
  quantities the strategy looked at — this is the feature snapshot, for free.

### Measured constraints

| | |
| --- | --- |
| `Qwen3.6`, `think=False`, JSON-schema-constrained | **~8 s/call**, valid JSON every time |
| `gemma4:31b` | 56 s and empty output — unusable |
| K-line PNG via matplotlib | 0.085 s, 19 KB; image adds no latency penalty |
| Latency budget per decision (5-min bars) | 300 s. 8 s uses **2.7%** of it |
| Entry candidates on `m5_mine` after the book limit | **2,907** → ~6.5 h of inference |
| 1-minute data coverage | 2026-02-02 → 2026-08-26 — **does not overlap** `m5_mine` or `m5_validate` |

## Decision

### 1. Insertion point: between `generate()` and `run()`

```
context  = build_context(config)
signals  = strategy.generate(context)      # baseline, untouched
signals  = validator.apply(signals, ...)   # NEW, optional
result   = BacktestEngine(...).run(context, signals)
```

`runner.execute()` gains one optional argument. With it absent the code path is
byte-identical to today's, so the baseline cannot regress.

**Rejected alternative:** passing a confirmation frame *into* the strategy (the
`sr_momentum.confirmation` pattern used for Kronos). It couples the filter to
one strategy, requires the strategy to be re-run, and — measured three times in
R09/R10/R11 — lets a veto *free a position slot* that `_respect_book_limit`
refills with a weaker candidate, so trade count rises and the comparison stops
being a comparison. Operating on final `target_weights` after the book limit
means a veto goes flat and nothing replaces it. That is the only way the two
arms stay comparable.

### 2. Candidates are derived generically, not published by strategies

A candidate is a bar where a symbol's target weight goes from flat to non-flat,
or flips sign. That is computable from `target_weights` alone, so **all four
registered strategies are supported with no strategy changes**.

### 3. The LLM may only veto

Its output narrows the position set; it can never open one, enlarge one, or
change a direction. `confidence` is recorded but **must not** scale position
size — sizing stays with the strategy. Vetoing zeroes the whole holding run from
the vetoed entry until the weight next changes, not just the entry bar.

### 4. Structured output only

Ollama's `format=<json schema>` constrains generation, so parsing cannot fail on
prose. A response that still fails validation is an `abstain`, not a guess.
No free-text parsing decides a trade.

### 5. Failure handling is explicit and configurable

| condition | behaviour |
| --- | --- |
| timeout, service down, malformed output, schema violation | `abstain` |
| snapshot has insufficient history | `abstain` |
| latency exceeds the decision→fill interval | candidate **expires**, dropped |
| what an `abstain` does | `on_abstain: keep` (default) or `drop` |

Default `keep`: the LLM is an enhancement, and an unavailable model must not
silently mutate the strategy. `drop` is available for a fail-closed live posture.

### 6. Everything is journalled

One append-only JSONL row per candidate: candidate, full feature snapshot,
rendered prompt, prompt version hash, model and options, raw output, parsed
decision, latency, and the final action taken. Decisions are cached by content
hash, so a re-run is free and reproducible without the model.

### 7. Latency is simulated, not assumed

The recorded wall-clock latency is replayed in the backtest. A decision that
would not have returned before the execution bar's open expires. At 5-min bars
and 8 s this never binds; the check exists because at 1-minute bars it would.

## Consequences

* Baseline behaviour is unchanged and provably so — the validator is opt-in.
* The comparison is clean: same data, same candidates, veto-only, no slot reuse.
* 6.5 h of inference for a full `m5_mine` arm. Acceptable overnight; the cache
  makes every subsequent analysis free.
* **1-minute context is not available on `m5_mine`.** Either 1-minute bars are
  downloaded for that window, or the higher-timeframe requirement is met with
  5-minute execution context plus a coarser aggregate. Downloading is preferred
  and is a data task, not a design one.
* `qtrader/decision/` is a new top-level package. It depends on `strategies` and
  `data`, and nothing depends on it — a strictly additive leaf.

## Alternatives Considered

* **LLM as an independent alpha source** — rejected by the brief and by the
  evidence: nothing in this project has shown an edge that survives costs, and
  an unconstrained generator would be the least testable thing yet built.
* **Per-bar streaming callback in the engine** — would require restructuring the
  engine's vectorised timing model, the one component with the strongest
  anti-lookahead guarantees. Not worth it for a filter.
* **Confidence-scaled position sizing** — explicitly excluded by the brief, and
  R10 §5h showed this system's sizing is already anti-correlated with its edge.
