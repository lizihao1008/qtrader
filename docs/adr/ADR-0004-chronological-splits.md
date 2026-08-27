# ADR-0004: Evaluation windows are named, contiguous, and declared before results are seen

## Status

Accepted — 2026-08-26

## Context

The M1 result was a warning: parameters chosen on 21 sessions produced +4.16%
in-sample and −14.86% out of sample. Nothing about that failure was subtle, and
it would have been caught earlier if the project had had a place to say *which
window is allowed to answer which question*.

Two failure modes needed to be closed off, and neither is prevented by good
intentions:

* **Sliding boundaries.** If the split is decided while looking at results, it
  moves to wherever the answer improves.
* **Amnesia about contamination.** A window used to choose a parameter cannot
  later measure that parameter, but nothing in a results directory records that
  fact. Six weeks later it is just another number in a table.

The conditional analysis in [R01](../research/R01-conviction-and-reversion.md)
made this urgent: mining sixteen features against one outcome needs a
confirmation window that is genuinely untouched.

## Decision

* Windows live in one file, `config/splits.yaml`, as contiguous date ranges.
  They are fixed before the analysis that uses them is run.
* Each split carries a **purpose string** describing what it may be used for.
  The purpose is part of the data, printed by the scripts and shown in the
  episode report — not a comment someone has to go looking for.
* Three splits exist today: `mine` (hypothesis generation, in-sample by
  definition), `validate` (one confirmation, then spent), and `burned`
  (the window that chose the current parameters — explicitly labelled as unable
  to test them).
* `apply_split` suffixes the `run_id`, so results from two windows can never
  overwrite each other.
* Random splits of time-series rows remain forbidden (CLAUDE.md §11.3).

## Consequences

* A confirmation is a single, recorded event. Once `validate` has answered a
  question about a strategy, re-running it with a tweaked parameter is visibly
  a second training pass rather than an innocent retry.
* The project is honest about having no clean test window for the current
  parameters. That is uncomfortable and correct: `burned` exists so nobody
  reports it as evidence.
* More data will need new splits rather than a redefinition of these. Extending
  `validate` after seeing its result would silently undo the whole point.
* Walk-forward evaluation, when it arrives, generalises this: many small
  train/test pairs instead of three fixed windows. The purpose string survives
  that change; the fixed dates do not.

## Alternatives Considered

* **`--start`/`--end` on the command line** — flexible, and exactly the problem:
  the boundary lives in shell history, where nobody can audit it.
* **A fixed fraction (60/20/20) computed from whatever data exists** — the
  boundaries move every time the dataset grows, so yesterday's validation window
  becomes today's training window without anyone deciding that.
* **Immediate walk-forward** — the right long-term answer, but it needs an
  evaluation harness that does not exist yet, and it would not have recorded the
  contamination that `burned` documents.
