# ADR-0006: Ideas are rejected on paper before they are implemented

## Status

Accepted — 2026-08-26

## Context

The project's measurement machinery is good: rank IC by implementation lag,
non-overlapping significance, costs calibrated from the data, shuffle tests
against matched nulls, attribution of a shortfall to signal / holding /
frequency / cost, and a ledger that keeps the multiple-testing burden honest.
Every conclusion it produced was correct as far as it went.

What was missing was a filter *in front* of it. Three strategies reached full
implementation — a moving-average crossover, a MACD-triggered trend follower,
and a cross-sectional reversal book — and all three were then falsified. None of
them needed a backtest to be doubted:

* the first two are functions of past prices over a window, publicly saturated,
  and have no mechanism at intraday horizons;
* the third has a real mechanism, but one that pays the liquidity *provider*
  while the implementation was a liquidity *taker*.

Each cost days of work to reject. The measurement was never the bottleneck; the
selection was.

## Decision

An idea is written down against `docs/research/GATE.md` before any strategy code
exists, and must survive all of:

1. a named mechanism with a counterparty who has a non-profit-seeking reason to
   trade against it;
2. a reason it has not already been arbitraged away;
3. an answer to *which side of the trade is paid* and whether this project's
   execution can be on that side;
4. an expected edge, stated before backtesting, that is several times the
   ~2.4 bps round-trip cost;
5. incremental information — not another transform of past prices;
6. a named failure regime and a capacity estimate.

Failing any of the first four is a rejection, recorded as such, not a caveat.

Explicitly rejected as a class, without further testing: oscillator and
moving-average variants, breakout rules, and any further reparameterisation of
trailing-return signals. These are not new experiments.

## Consequences

* Far fewer strategies get implemented, which is the point. Research time moves
  from running backtests to deciding what deserves one.
* Some real effects will be declined because this project cannot access them —
  notably anything living inside the spread. That is correct: an effect that
  requires passive execution and a faster feed is not an opportunity here
  however real it is.
* The existing strategies stay in the tree but are relabelled. `ma_cross` is a
  plumbing fixture, not a candidate. `trend_ratchet` and
  `cross_sectional_residual` are retired as candidates and retained as worked
  examples with their falsifications documented.
* The gate can reject something that would have worked. That is an accepted
  cost: the alternative is the regime that produced three implemented failures,
  and a false negative is cheaper than the false positives were.
* Applied honestly, the gate currently rejects **everything in the repository**,
  and would reject most ideas proposable within intraday US large caps on IEX
  data. That is information about the venue, and it is recorded in the gate
  document rather than treated as a reason to lower the bar.

## Alternatives Considered

* **Keep backtesting and rely on out-of-sample discipline.** This is what was
  being done, and the discipline held — every result was correctly falsified.
  But rejecting after three days of implementation is far more expensive than
  rejecting after ten minutes of thought, and the volume of tests inflates the
  multiple-testing burden for everything that follows.
* **A prior-probability score instead of hard gates.** More flexible, and
  weaker: a scoring rule invites arguing a favoured idea across the line. The
  first four criteria are pass/fail because that is what stops the arguing.
* **Automated idea generation and mass screening.** Explicitly rejected. It
  optimises for finding a good-looking curve, which is the failure mode this
  decision exists to prevent.
