# ADR-0008: Import the market_state consolidation detector rather than copy it

## Status
Accepted

## Context

The instruction was to gate `sr_momentum` on the 横盘 (consolidation) detector
that already exists in the sibling repository `/Users/zihao/work/market_state`,
not to write a new one. Three ways to get it:

1. copy `structure/consolidation.py` into `qtrader/features/`;
2. import `market_state` from `qtrader`;
3. precompute the mask into a parquet artifact and read that.

(1) creates the parallel implementation §9 forbids: two copies of a state
machine that will drift, with the leakage suite that makes it trustworthy left
behind in the other repo. (3) decouples but goes stale silently, and hides which
detector version produced a result.

`market_state` already reads the clean parquet store *this* repo writes, at the
same feed and timeframe, so the two see identical bars. A code dependency in the
other direction closes a loop at the repository level, which is the real cost of
(2).

## Decision

Import it, and confine the dependency to one module in the **research** layer:
`src/qtrader/experiments/consolidation_gate.py`.

`SRMomentumStrategy` gains a generic `entry_veto` — a `timestamp x symbol`
boolean frame that refuses new positions — and learns nothing about ranges. The
production strategy layer therefore has no `market_state` import, and any other
regime detector can be tested as a gate by producing the same frame.

The detector's own config file is read as-is. Overriding its thresholds from
this repo would be tuning someone else's detector against this repo's P&L.

Installed into the `quant` env with `pip install -e <path> --no-deps`: the
detector needs only numpy/pandas/PyYAML, and a full install would pull
scikit-learn and scipy in for code this repo never calls.

## Consequences

* The dependency is one-way in code and one-way in data, but the two directions
  are opposite. Neither repo may import the other outside these boundaries:
  `market_state` reads this repo's *data*, `qtrader/experiments` imports that
  repo's *code*. A `market_state` import anywhere under `qtrader/strategies/`,
  `features/`, `backtest/` or `execution/` is a defect.
* A `market_state` refactor can break `qtrader`'s experiment layer. Accepted:
  the alternative is a silent copy that cannot break, and is wrong instead.
* `tests/unit/test_consolidation_gate.py` skips when the package is absent, so
  the suite still runs in an environment without the sibling checkout.
* The adapter pins the grid: the detector's thresholds are stated in 5-minute
  bars, so it always runs on the 5-minute series and is carried to a finer
  decision grid with `features.multiframe.align_to_fine`.

## Alternatives Considered

* **Reimplement the seed/hold/pending/exit machine in `qtrader/features/`** —
  rejected under §9 and §10. The value of the detector is its leakage suite, and
  a copy does not inherit it.
* **A config flag on the strategy (`no_entry_in_range: true`)** — rejected:
  it would put a `market_state` import inside the strategy layer to satisfy a
  YAML boolean, coupling production code to a research prototype.
