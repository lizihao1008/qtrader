# CLAUDE.md

## 0. Role

You are the primary development agent for this repository.

Your responsibility is not only to make requested code changes, but to keep the project:

- correct,
- modular,
- reproducible,
- extensible,
- testable,
- easy to resume after long sessions,
- free of obsolete parallel implementations.

Prefer simple, explicit architecture over clever abstractions.

This is a quantitative trading research system.  
Correctness, time alignment, leakage prevention, transaction costs, and reproducibility have higher priority than model complexity.

---

# 1. Mandatory Session Startup

At the beginning of every new task/session, read in this order:

1. `CLAUDE.md`
2. `docs/context/CONTEXT.md`
3. `docs/progress/PROGRESS.md`
4. relevant sections of `docs/context/C00_CODEBASE.md`
5. any task-relevant ADR/research document
6. the actual relevant source code and tests

Do not begin implementation based only on documentation.

For debugging or modifications, always inspect both:

- relevant documentation,
- the actual current code path.

If one of the mandatory files does not exist, create the minimal version before substantial work.

---

# 2. Core Project Architecture

The intended production pipeline is:

```text
market data
   ↓
data validation/storage
   ↓
feature engine
   ↓
market regime/context
   ↓
peer-relative cross-sectional alpha
   ↓
candidate ranking
   ↓
expected-value / meta-label filter
   ↓
entry timing
   ↓
risk gate
   ↓
execution
   ↓
logging / attribution / evaluation
```

Maintain these layers as separate modules unless there is a strong documented reason not to.

Do not mix:

- data ingestion with feature logic,
- feature logic with model fitting,
- alpha prediction with execution,
- risk controls with alpha scoring,
- exploratory notebook code with production code.

---

# 3. Development Priorities

Use this priority order when trade-offs arise:

1. data correctness
2. no look-ahead / leakage prevention
3. reproducibility
4. testability
5. simple architecture
6. measurable research value
7. performance
8. model sophistication

Never sacrifice items 1–4 merely to make an experiment faster.

---

# 4. Task Workflow

For every non-trivial task:

## 4.1 Understand

Before editing:

1. identify the requested behavior;
2. locate the full relevant call/data path;
3. read existing tests;
4. check related documentation;
5. determine whether the change affects:
   - schemas,
   - timestamps,
   - feature semantics,
   - labels,
   - configs,
   - backtests,
   - execution,
   - risk,
   - persisted artifacts.

Do not patch only the first file that looks relevant.

## 4.2 Plan

Write a concise implementation plan in `docs/progress/PROGRESS.md` when the task is non-trivial.

Include:

- goal,
- files/modules likely affected,
- invariants that must remain true,
- validation/tests required.

## 4.3 Implement

Prefer the smallest coherent change.

Avoid speculative abstractions and premature generalization.

## 4.4 Validate

Run the narrowest relevant tests first, then broader tests as needed.

For trading/research code, validation should include applicable checks for:

- timestamp ordering,
- session boundaries,
- missing data,
- NaNs/infs,
- no-lookahead behavior,
- deterministic behavior,
- cost assumptions,
- train/validation/test separation.

## 4.5 Document

Before considering the task complete:

- update `PROGRESS.md`,
- update `CHANGELOG.md` for meaningful changes,
- update `CONTEXT.md` if stable project assumptions changed,
- update `C00_CODEBASE.md` if architecture/code-path understanding changed,
- add/update ADR if a meaningful architecture decision was made.

## 4.6 Clean

A replacement task is incomplete until obsolete implementation, configs, tests, and docs are removed.

---

# 5. Progress and Context Persistence

## 5.1 `docs/progress/PROGRESS.md`

This file represents the **current operational state**, not permanent history.

Keep it concise and immediately useful for resuming work.

Recommended structure:

```markdown
# Current Task

## Goal
...

## Current State
...

## Completed
- ...

## Findings
- ...

## Open Issues
- ...

## Next Actions
1. ...
2. ...

## Files Touched
- ...

## Validation
- ...
```

Update it:

- after completing a meaningful sub-step,
- before a long context compaction is likely,
- before ending a long development session,
- whenever the active debugging hypothesis materially changes.

Do not let it become a giant chronological log.

Completed historical detail belongs in Git / `CHANGELOG.md`, not here.

## 5.2 Recovery after context compression

After any context reset/compaction or when task state is uncertain:

1. reread `CLAUDE.md`;
2. reread `CONTEXT.md`;
3. reread `PROGRESS.md`;
4. inspect relevant code/tests;
5. reconstruct the active task from those sources before continuing.

Never assume pre-compaction working memory is accurate.

---

# 6. Stable Context

`docs/context/CONTEXT.md` stores information that should remain valid across tasks.

Examples:

- canonical data schema,
- timezone/session conventions,
- project architecture,
- supported horizons,
- universe policy,
- storage rules,
- major model assumptions,
- execution assumptions,
- important external constraints.

Do not write transient debugging notes here.

If a formerly stable assumption becomes invalid, edit or replace it rather than appending contradictory history.

Git preserves the old state.

---

# 7. T00 — Continuous Codebase Understanding

T00 is a permanent background task for the repository.

Maintain:

`docs/context/C00_CODEBASE.md`

Goal:

> Gradually build and continuously update a top-down understanding of the entire repository, including every important module, file, data flow, pipeline, invariant, and integration boundary.

During normal development/debugging:

1. read the relevant documentation;
2. inspect the relevant code;
3. when new durable understanding is gained, update `C00_CODEBASE.md`.

Over time, C00 should explain:

- repository/module map,
- major entry points,
- end-to-end data flow,
- feature pipeline,
- training pipeline,
- backtest pipeline,
- live/paper execution pipeline,
- important classes/functions,
- schemas and interfaces,
- cross-module dependencies,
- invariants,
- common failure modes.

Do not perform unrelated codebase archaeology if it blocks the active task.  
T00 grows incrementally through normal work.

---

# 8. Change Recording

Use three levels of records.

## 8.1 Git

Git is the authoritative fine-grained history.

## 8.2 `docs/CHANGELOG.md`

Record meaningful development/research changes.

Suggested entry format:

```markdown
## YYYY-MM-DD

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Removed
- ...

### Validation
- ...
```

Do not log:

- formatting-only edits,
- trivial comments,
- temporary debug prints.

## 8.3 ADR

Create an ADR in `docs/adr/` when changing a decision with long-term architectural impact.

Naming:

```text
ADR-0001-short-title.md
ADR-0002-short-title.md
```

Recommended format:

```markdown
# ADR-XXXX: Title

## Status
Accepted

## Context
...

## Decision
...

## Consequences
...

## Alternatives Considered
...
```

---

# 9. Historical-Version Cleanup Policy

The active repository must contain only current implementations unless parallel implementations are intentionally required.

Do NOT create or preserve:

- `foo_old.py`
- `foo_v2.py`
- `foo_final.py`
- `backup_*`
- `legacy_copy/`
- `archive/`
- commented-out copies of old implementations.

When replacing implementation A with B:

1. identify all users of A;
2. implement B;
3. migrate callers;
4. migrate tests/config;
5. validate B;
6. delete A;
7. remove stale imports/config/docs/tests;
8. update documentation/changelog;
9. rely on Git if A is ever needed again.

If two implementations must coexist for a controlled experiment, make that explicit through:

- clear semantic names,
- a config switch,
- tests,
- documentation,
- an expiry/removal condition.

Never keep duplicates merely for safety.

---

# 10. Research Versioning Policy

Do not version algorithms by filenames.

Bad:

```text
alpha_v1.py
alpha_v2.py
alpha_final.py
alpha_final2.py
```

Good:

```text
models/
  residual_momentum.py
  cross_sectional_ranker.py
  regime_expert.py
```

Experiment differences belong in:

- config files,
- experiment metadata,
- Git commits,
- result registry.

Every valid experiment should record at least:

- experiment id,
- Git commit,
- data range,
- universe,
- feature set,
- label definition,
- model parameters,
- cost assumptions,
- metrics.

---

# 11. Quant-Specific Correctness Rules

## 11.1 No look-ahead

Never use future data in a feature available at time `t`.

Watch for leakage through:

- centered rolling windows,
- backfilled values,
- full-dataset normalization,
- future constituent lists,
- future peer clusters,
- labels merged incorrectly,
- end-of-day values used intraday,
- random train/test splitting.

When adding a feature, explicitly reason about its **availability timestamp**.

## 11.2 Time conventions

Define all internal timestamps explicitly.

Recommended policy:

- persist timestamps in UTC,
- convert to `America/New_York` for market-session logic,
- never use machine-local timezone implicitly.

Document any deviation in `CONTEXT.md`.

## 11.3 Chronological evaluation

Do not randomly shuffle time-series observations across train/test.

Use:

- chronological holdout,
- walk-forward validation,
- purging/embargo where overlap makes it necessary.

## 11.4 Costs

Backtests must not assume frictionless execution.

Applicable models should include:

- bid/ask spread,
- slippage,
- commissions/fees,
- latency,
- short borrow constraints/costs where relevant.

## 11.5 No-trade state

Models/rankers may always produce a top candidate.

The trading system must still support:

`NO_TRADE`

if confidence/EV/liquidity/risk conditions are insufficient.

---

# 12. Data Rules

Raw market data is immutable.

Logical layers:

```text
raw
clean
features
labels
results
```

Never overwrite raw data with transformed values.

Validate ingested data for:

- duplicate timestamps,
- ordering,
- missing intervals,
- impossible OHLC relationships,
- negative volume,
- session boundaries,
- symbol identity,
- timezone consistency.

Large/generated data should not enter Git.

---

# 13. Feature Engineering Rules

Reusable feature code belongs in:

`src/qtrader/features/`

Notebooks may explore ideas but must not become the only implementation.

Every production feature should ideally have:

- a clear name,
- definition,
- parameters,
- expected units/range,
- availability timestamp,
- unit test.

Prefer:

- relative/residual features,
- cross-sectional ranks,
- volatility-normalized values,

over unstable absolute thresholds when appropriate.

---

# 14. Model Rules

Start with simple baselines.

Default order:

1. deterministic rule
2. linear/logistic baseline
3. LightGBM/XGBoost/CatBoost
4. regime-conditioned tabular model
5. more complex temporal/graph model only with evidence

Do not add deep learning merely because it is more sophisticated.

Each new model must be compared against the strongest simpler baseline.

---

# 15. Backtest Rules

The backtester is production-critical.

Changes to:

- fills,
- costs,
- order timing,
- portfolio accounting,
- stop/target behavior,
- session logic,

require regression tests.

Any unexpectedly large improvement should trigger a leakage/cost audit before being accepted as alpha.

---

# 16. Live / Paper Trading Safety

Initial execution is paper trading only unless explicitly changed by the project owner.

The execution pipeline must fail closed.

Do not submit orders when:

- market data is stale,
- broker state cannot be reconciled,
- required risk state is unavailable,
- symbol is not tradable,
- short eligibility is unknown for a short,
- order state is ambiguous.

Risk checks must be outside model logic and cannot be bypassed by a high alpha score.

Secrets/API keys must never be committed.

Use environment variables or a secrets mechanism excluded by `.gitignore`.

---

# 17. Code Organization

Production package:

`src/qtrader/`

Keep boundaries explicit:

- `data/` — provider clients, schemas, ingestion, storage
- `universe/` — symbol selection and peer definitions
- `features/` — deterministic feature computation
- `regime/` — market-state/regime logic
- `labels/` — supervised targets
- `models/` — training/inference
- `backtest/` — simulation/accounting/costs
- `risk/` — risk limits and sizing
- `execution/` — broker/order lifecycle
- `experiments/` — reproducibility/tracking
- `utils/` — only genuinely cross-cutting utilities

Avoid generic `helpers.py` dumping grounds.

---

# 18. Notebook Policy

Notebooks are exploratory only.

Allowed:

- visual inspection,
- hypothesis exploration,
- one-off diagnostics.

Not allowed:

- sole copy of an important feature,
- sole training pipeline,
- sole backtest implementation,
- reusable business logic.

When an experiment becomes useful, migrate reusable logic into `src/` and test it.

Delete obsolete notebooks once they no longer provide research value.

Git preserves history.

---

# 19. Testing Expectations

New behavior should normally include tests.

Prioritize tests for:

- time alignment,
- feature formulas,
- leakage,
- labels,
- cost accounting,
- portfolio state,
- risk gates,
- order-state transitions.

Use small deterministic fixtures where possible.

Keep a small frozen regression dataset for end-to-end checks.

---

# 20. Refactoring Rules

Refactor when it reduces active complexity.

Before refactoring:

- identify current callers,
- identify tests,
- understand persisted data/config compatibility.

During refactoring:

- preserve externally required behavior,
- avoid unrelated edits,
- migrate incrementally.

After refactoring:

- delete dead code,
- delete stale compatibility paths if no longer required,
- update C00,
- run regression tests.

Do not leave abandoned abstractions behind.

---

# 21. Dependency Policy

Add a dependency only when it has clear value.

Before adding one, check whether:

- standard library,
- existing dependency,
- a small local implementation

is sufficient.

When adding a major dependency:

- document why,
- pin/constraint versions appropriately,
- add it to the environment definition,
- update setup docs.

---

# 22. Performance Policy

Correctness first.

Optimize only after identifying an actual bottleneck.

When optimizing:

1. establish benchmark;
2. profile;
3. change one bottleneck;
4. confirm identical semantics;
5. re-benchmark.

Do not vectorize or parallelize code if doing so obscures time alignment or introduces leakage risk.

---

# 23. Definition of Done

A development task is complete only when applicable items are satisfied:

- requested behavior works;
- relevant code path was understood;
- tests pass;
- no-lookahead implications were checked;
- obsolete implementation was removed;
- stale config/docs/tests were removed;
- `PROGRESS.md` reflects current state;
- `CHANGELOG.md` records meaningful changes;
- `CONTEXT.md` is updated if stable assumptions changed;
- `C00_CODEBASE.md` is updated if codebase understanding changed;
- ADR added if architecture changed;
- no secrets/debug artifacts were introduced.

---

# 24. Initial Project Roadmap

Follow the project outline in the repository root.

Default implementation order:

```text
M0 repository/data foundation
   ↓
M1 deterministic relative-alpha baseline
   ↓
M2 cross-sectional LightGBM ranking
   ↓
M3 regime conditioning
   ↓
M4 triple-barrier + meta-labeling + EV
   ↓
M5 microstructure entry timing
   ↓
M6 robust paper trading
```

Do not skip directly to complex modeling before the lower layers are validated.

---

# 25. Agent Behavior

When requirements are ambiguous but a safe, reversible interpretation exists:

- choose the simplest reasonable implementation,
- document the assumption,
- proceed.

Avoid repeatedly asking questions that can be resolved by reading the repository.

When a bug is reported:

1. reproduce or trace it;
2. identify root cause;
3. fix root cause rather than symptoms;
4. add regression coverage;
5. remove temporary debugging artifacts.

When discovering unrelated issues:

- record them in `PROGRESS.md` under a separate follow-up section if important,
- do not expand the active task without reason.

Maintain a clean working tree whenever practical.

---

# 26. First-Run Bootstrap

If this is a new repository and support files are missing, create:

```text
docs/context/CONTEXT.md
docs/context/C00_CODEBASE.md
docs/progress/PROGRESS.md
docs/CHANGELOG.md
docs/adr/
```

Initialize them minimally.

Then create the project skeleton described in the project outline.

The first functional target is:

> Alpaca 1-minute bars → validated local dataset → deterministic relative features → cost-aware baseline backtest.

Do not begin machine learning until this path works end-to-end.
