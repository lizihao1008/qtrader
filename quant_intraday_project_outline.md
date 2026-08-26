# Intraday Quant Trading System — Initial Project Outline

> Working name: `qtrader`
>
> Goal: build a modular, research-driven intraday long/short equity trading system using Alpaca market data.  
> Core idea: **market regime → peer-relative alpha → cross-sectional ranking → entry timing → execution/risk control**.

---

## 1. Project Objective

Build an intraday US-equity trading system that, at each decision time:

1. Estimates the current **market regime / market state**.
2. Measures each stock relative to:
   - the broad market,
   - its sector,
   - its dynamic peer group.
3. Produces a **cross-sectional long/short alpha score**.
4. Selects only the highest expected-value opportunity.
5. Uses a separate **entry-timing model** to decide `BUY / SHORT / WAIT`.
6. Executes only when expected return remains positive after:
   - spread,
   - slippage,
   - fees,
   - borrow/short constraints.
7. Records every decision for reproducible research and post-trade analysis.

The system should explicitly support **no-trade** decisions.

---

## 2. Guiding Principles

### 2.1 Research before complexity

Start with strong tabular baselines before deep learning.

Recommended evolution:

`rules → LightGBM/XGBoost → regime-conditioned models → microstructure → graph models → multimodal/news`

Do **not** begin with RL, Transformers, or GNNs unless simpler models have been exhausted.

### 2.2 Separate alpha from timing

The project must keep two questions separate:

- **Alpha model:** which stock/direction has the best opportunity?
- **Entry model:** is *now* a good time to enter?

This prevents the cross-sectional model from being overloaded with micro-timing behavior.

### 2.3 Optimize expected value, not win rate

A signal is useful only if its expected value after costs is positive.

Conceptually:

`EV = P(win) * E(win) - P(loss) * E(loss) - trading_cost`

The final system should rank candidate trades by expected value rather than raw probability of price increase.

### 2.4 Avoid look-ahead leakage

All features at time `t` must be computable using information available at or before `t`.

This rule applies to:

- feature normalization,
- cross-sectional ranks,
- peer clustering,
- volatility estimates,
- labels,
- training/test split,
- market regime estimation.

### 2.5 Git is the historical archive

Do not keep historical implementation copies inside the active codebase.

Forbidden patterns include:

- `model_old.py`
- `strategy_v2.py`
- `strategy_final.py`
- `backup/`
- `old/`
- `archive/`
- duplicated notebooks representing obsolete implementations.

Once a replacement is validated:

1. migrate callers/tests,
2. delete the obsolete implementation,
3. update documentation,
4. rely on Git history for recovery.

---

# 3. Core Trading Architecture

```text
                    Alpaca Market Data
                           |
             +-------------+--------------+
             |                            |
        Historical                      Realtime
      bars/trades/quotes          websocket stream
             |                            |
             +-------------+--------------+
                           |
                     Data Layer
                           |
                    Feature Engine
         +-----------------+-----------------+
         |                 |                 |
    Market State      Peer Features     Stock Features
         |                 |                 |
         +-----------------+-----------------+
                           |
                    Market Regime
                           |
                Cross-sectional Alpha
                           |
              Long / Short Candidate Rank
                           |
                    Expected Value
                           |
                   Candidate Filter
                           |
                     Entry Model
                           |
                  BUY / SHORT / WAIT
                           |
                 Risk + Execution Layer
                           |
                       Broker API
                           |
                Logging / Evaluation
```

---

# 4. Initial Universe

Start with a deliberately constrained and liquid universe.

Suggested V1:

- S&P 100 / Nasdaq-100 constituents, or
- 50–150 highly liquid US equities.

Include broad and sector reference ETFs:

- SPY
- QQQ
- IWM
- XLK
- XLF
- XLE
- XLV
- XLY
- XLP
- XLI
- XLB
- XLU
- XLRE
- XLC

Initial filters may include:

- minimum average daily dollar volume,
- maximum spread,
- minimum price,
- tradable status,
- shortable / borrow status when short signals are enabled.

Universe membership must be time-aware when used in historical backtests.

---

# 5. Data Layer

## 5.1 Raw data to store

Prefer storing the lowest practical resolution and deriving aggregates offline.

### Required for V1

- 1-minute OHLCV bars
- corporate-action-adjusted metadata where applicable
- symbol/universe metadata
- reference ETF bars

### Recommended for V2+

- trades
- quotes
- bid/ask spread
- bid/ask sizes
- trade intensity
- market microstructure features

### Optional later

- news
- earnings/calendar events
- fundamentals
- options-derived features
- alternative sentiment data

## 5.2 Storage rules

Raw data must be immutable.

Recommended logical layers:

```text
raw      -> data exactly as received
clean    -> validated/canonicalized data
features -> reproducible derived features
labels   -> training targets
results  -> backtests/model outputs
```

Never manually edit raw market data.

Every derived dataset should be reproducible from:

- raw input,
- configuration,
- code version.

---

# 6. Feature System

Feature definitions must live in reusable production code, not only in notebooks.

## 6.1 Market-state features

Examples:

- SPY 1/5/15/30/60-minute returns
- QQQ 1/5/15/30/60-minute returns
- IWM returns
- sector ETF returns
- percentage of universe above VWAP
- percentage of universe positive on day
- cross-sectional return dispersion
- market realized volatility
- market relative volume
- opening gap breadth
- high/low breadth
- sector dispersion / rotation

Output:

`market_state[t]`

## 6.2 Stock features

### Price / momentum

- return_1m
- return_3m
- return_5m
- return_15m
- return_30m
- return_since_open
- overnight_gap
- distance_to_day_high
- distance_to_day_low

### VWAP

- price_to_vwap
- normalized_vwap_distance
- vwap_slope
- fraction_time_above_vwap
- recent_vwap_cross

### Volume

- relative volume by time-of-day
- rolling volume acceleration
- volume z-score
- dollar volume

### Volatility

- ATR-like intraday range
- realized volatility
- range expansion
- volatility acceleration

### Candle structure

- body/range
- upper wick ratio
- lower wick ratio
- close location value
- opening-range breakout flags

## 6.3 Relative / residual features

These are first-class features, not optional extras.

For each stock:

- return minus SPY return
- return minus QQQ return
- return minus sector return
- return minus peer-basket return
- residual momentum
- residual volatility
- relative VWAP distance
- relative volume rank

## 6.4 Cross-sectional ranks

At each timestamp calculate ranks such as:

- 5m return rank
- 15m residual return rank
- 30m residual return rank
- RVOL rank
- VWAP-distance rank
- volatility rank

Ranks must be computed using only the universe visible at that timestamp.

---

# 7. Peer-Group System

Implement in stages.

## V1 — static peers

Use sector / industry classifications.

## V2 — dynamic statistical peers

Build rolling similarity using:

- return correlation,
- beta,
- intraday pattern similarity,
- volatility,
- volume behavior.

Possible algorithms:

- hierarchical clustering,
- spectral clustering,
- k-means on learned/statistical representations.

## V3 — graph representation

Stocks become graph nodes.

Potential edges:

- rolling correlation,
- same industry,
- ETF co-membership,
- lead/lag relationship,
- learned similarity.

A graph model is only justified after simpler peer-relative features establish measurable alpha.

---

# 8. Market-Regime Layer

V1 should remain interpretable.

Possible models:

- rules
- K-means
- Gaussian Mixture Model
- Hidden Markov Model

Example conceptual regimes:

1. strong bullish trend
2. strong bearish trend
3. high-volatility chop
4. low-volatility chop
5. sector-rotation / high-dispersion
6. market-wide risk-off

The regime layer should expose either:

- a discrete regime label, or
- a probability vector over regimes.

Later models may condition their predictions on this state.

---

# 9. Labels

Do not use only `future_price > current_price`.

## 9.1 Baseline regression target

Future residual return over horizons such as:

- 5 min
- 15 min
- 30 min
- 60 min

## 9.2 Triple-barrier target

For each candidate entry define:

- profit barrier,
- stop barrier,
- maximum holding time.

Record:

- barrier hit first,
- realized return,
- maximum favorable excursion (MFE),
- maximum adverse excursion (MAE),
- time to exit.

Barrier widths should preferably scale with volatility.

## 9.3 Ranking target

The cross-sectional model should learn which stocks have superior forward outcomes **relative to other stocks at the same decision time**.

---

# 10. Modeling Roadmap

## V0 — deterministic baseline

Purpose:

- validate data correctness,
- validate backtester,
- establish a benchmark.

Candidate strategies:

- peer-relative momentum,
- residual mean reversion,
- VWAP pullback,
- opening-range breakout.

## V1 — cross-sectional tabular model

Preferred baseline:

- LightGBM
- XGBoost
- CatBoost

Tasks:

- regression,
- binary/triple-barrier classification,
- learning-to-rank.

Strong preference: test a **ranking formulation** because the production decision is cross-sectional selection.

## V2 — regime-conditioned models

Options:

- regime as an input feature,
- separate expert model per regime,
- mixture-of-experts gating.

## V3 — meta-labeling

Primary model proposes trades.

Meta-model estimates:

- probability trade should be taken,
- expected payoff,
- confidence,
- possibly position size.

## V4 — microstructure entry model

Use quotes/trades for entry timing.

Potential features:

- spread
- spread percentile
- bid/ask imbalance
- quote intensity
- trade intensity
- short-term order-flow imbalance
- microprice
- aggressive-buy/sell imbalance

Output:

`ENTER_LONG / ENTER_SHORT / WAIT`

## V5 — dynamic peer graph

Introduce graph/temporal models only after measurable gain is expected.

## V6 — news/event filter

Use news primarily as:

- event-risk flag,
- mean-reversion veto,
- regime modifier,
- alpha context.

Do not initially treat generic sentiment as the core trading signal.

---

# 11. Backtesting Requirements

The backtester is a critical subsystem and must be treated as production code.

It must support:

- event/time ordered simulation,
- no look-ahead,
- long and short,
- configurable commissions,
- spread,
- slippage,
- latency assumptions,
- position limits,
- borrow/shortability constraints,
- stop / take-profit / time exits,
- multiple holding horizons,
- no-trade decisions.

## 11.1 Data splits

Never use random row splits.

Use chronological splits such as:

```text
train -> validation -> test
```

Later use walk-forward evaluation.

## 11.2 Required metrics

### Strategy metrics

- cumulative return
- annualized return
- Sharpe
- Sortino
- max drawdown
- Calmar
- hit rate
- profit factor
- average win/loss
- turnover
- exposure
- long/short contribution
- tail losses

### Signal metrics

- IC / rank IC
- precision at top-k
- return of top-k / bottom-k
- calibration
- EV by confidence bin
- performance by regime
- performance by sector
- performance by time of day

### Execution metrics

- expected vs realized entry
- spread paid
- slippage
- fill ratio
- opportunity decay after signal

---

# 12. Risk Management

Risk management must be separate from alpha generation.

Initial controls:

- max notional per position
- max portfolio gross exposure
- max directional net exposure
- max daily loss
- max single-trade loss
- max number of trades/day
- cooldown after repeated losses
- spread/liquidity filter
- volatility halt
- stale-data halt
- API/data-integrity halt

No model may bypass the risk layer.

---

# 13. Execution

## V1

Paper-trading only.

Support:

- marketable limit orders where appropriate,
- cancel/replace,
- timeout,
- position reconciliation,
- broker state reconciliation.

Every order should record:

- signal timestamp,
- decision price,
- submitted price,
- order timestamp,
- fill timestamp,
- fill price,
- spread,
- modeled EV,
- realized outcome.

---

# 14. Reproducibility

Every experiment should have:

```text
experiment_id
timestamp
git_commit
config
data_range
universe
feature_set
label_definition
model_parameters
cost_model
metrics
artifact_paths
```

A result without this metadata is not considered a valid experiment.

Configuration should be externalized rather than hard-coded.

Recommended:

- YAML/TOML config files
- deterministic seeds where practical
- explicit dataset date ranges

---

# 15. Proposed Repository Structure

```text
qtrader/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .gitignore
│
├── config/
│   ├── data/
│   ├── features/
│   ├── models/
│   ├── backtest/
│   └── live/
│
├── data/
│   └── .gitkeep
│
├── docs/
│   ├── context/
│   │   ├── CONTEXT.md
│   │   └── C00_CODEBASE.md
│   ├── progress/
│   │   └── PROGRESS.md
│   ├── adr/
│   ├── research/
│   └── CHANGELOG.md
│
├── notebooks/
│   └── exploratory_only/
│
├── scripts/
│   ├── download_data.py
│   ├── build_features.py
│   ├── train.py
│   ├── backtest.py
│   └── paper_trade.py
│
├── src/
│   └── qtrader/
│       ├── data/
│       │   ├── alpaca_client.py
│       │   ├── ingest.py
│       │   ├── schema.py
│       │   └── storage.py
│       │
│       ├── universe/
│       │   ├── filters.py
│       │   └── peers.py
│       │
│       ├── features/
│       │   ├── market.py
│       │   ├── stock.py
│       │   ├── relative.py
│       │   ├── ranks.py
│       │   └── microstructure.py
│       │
│       ├── regime/
│       │   ├── base.py
│       │   └── models.py
│       │
│       ├── labels/
│       │   ├── forward_returns.py
│       │   └── triple_barrier.py
│       │
│       ├── models/
│       │   ├── alpha/
│       │   ├── ranking/
│       │   ├── meta/
│       │   └── entry/
│       │
│       ├── backtest/
│       │   ├── engine.py
│       │   ├── costs.py
│       │   ├── portfolio.py
│       │   └── metrics.py
│       │
│       ├── risk/
│       │   ├── rules.py
│       │   └── sizing.py
│       │
│       ├── execution/
│       │   ├── broker.py
│       │   ├── alpaca.py
│       │   └── paper.py
│       │
│       ├── experiments/
│       │   ├── registry.py
│       │   └── tracking.py
│       │
│       └── utils/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── regression/
│
└── results/
    └── .gitkeep
```

Rules:

- `notebooks/` is exploratory only.
- reusable logic must migrate into `src/`.
- raw/large datasets and generated results must not be committed to Git.
- obsolete source files must be deleted, not renamed to `*_old` or `*_v2`.

---

# 16. Documentation System

## 16.1 `docs/context/CONTEXT.md`

Stable project context:

- current architecture,
- strategy assumptions,
- important conventions,
- data definitions,
- known constraints,
- resolved design choices.

It should not become a chronological diary.

## 16.2 `docs/progress/PROGRESS.md`

Current working state:

- active task,
- completed steps,
- current findings,
- unresolved issues,
- next concrete actions,
- temporary hypotheses.

This file is optimized for session recovery.

## 16.3 `docs/context/C00_CODEBASE.md`

Long-running codebase understanding document.

As the project grows, continuously document:

- each major module,
- important files/classes/functions,
- data flow,
- call relationships,
- invariants,
- failure modes.

The goal is to maintain a top-down map of the entire repository.

## 16.4 `docs/adr/`

Architecture Decision Records for meaningful design choices.

Examples:

- why Parquet/DuckDB was chosen,
- why ranking replaced classification,
- why a particular peer definition was selected,
- why an old subsystem was removed.

## 16.5 `docs/CHANGELOG.md`

Record meaningful user-visible or research-relevant changes.

Do not log every typo.

Each meaningful development task should leave a concise entry.

---

# 17. Version / Cleanup Policy

A feature replacement is incomplete until obsolete code is removed.

For every replacement:

1. identify old implementation and all callers,
2. migrate users,
3. update tests,
4. run relevant regression tests,
5. delete the old implementation,
6. remove stale config/docs/tests,
7. update `CHANGELOG.md`,
8. update `CONTEXT.md` / `C00_CODEBASE.md` if architecture changed.

Never maintain parallel implementations merely "in case".

Exceptions require an explicit ADR.

---

# 18. Testing Strategy

Minimum expectations:

### Unit tests

- feature calculations
- no-lookahead guarantees
- label construction
- cost model
- risk limits
- position accounting

### Integration tests

- Alpaca ingestion
- raw → clean → features
- model → signal
- signal → backtester
- paper-order lifecycle

### Regression tests

Protect previously verified behavior:

- feature values on frozen fixtures
- backtest metrics on a small frozen dataset
- order/risk state transitions

---

# 19. Initial Milestones

## M0 — Skeleton and data contract

Deliverables:

- repository structure
- configuration system
- Alpaca client abstraction
- 1-minute bar ingestion
- data validation
- local storage schema
- basic tests

## M1 — Deterministic baseline

Deliverables:

- market features
- stock features
- peer/sector-relative features
- cross-sectional ranks
- simple residual momentum strategy
- transaction-cost-aware backtest

Success criterion:

A fully reproducible end-to-end pipeline, even if alpha is weak.

## M2 — LightGBM ranking baseline

Deliverables:

- chronological dataset builder
- ranking labels
- LightGBM ranker
- top/bottom candidate analysis
- walk-forward evaluation
- feature importance / SHAP diagnostics

## M3 — Regime conditioning

Deliverables:

- regime feature layer
- interpretable regime detector
- per-regime evaluation
- comparison:
  - unconditional model
  - regime-feature model
  - regime-specific expert models

## M4 — Meta-labeling and expected value

Deliverables:

- triple-barrier labels
- MFE/MAE statistics
- meta-model
- cost-adjusted EV estimation
- explicit no-trade threshold

## M5 — Entry timing

Deliverables:

- quote/trade ingestion
- microstructure features
- entry model
- signal-to-fill analysis

## M6 — Paper trading

Deliverables:

- realtime pipeline
- risk controls
- order state machine
- reconciliation
- persistent decision log
- dashboard/reporting

Only after stable paper-trading behavior should live deployment be considered.

---

# 20. First Development Tasks

Recommended order:

1. initialize repository and directory structure;
2. create `CONTEXT.md`, `PROGRESS.md`, `C00_CODEBASE.md`, `CHANGELOG.md`;
3. define configuration and secrets handling;
4. implement Alpaca client abstraction;
5. define canonical 1-minute bar schema;
6. download a small universe and validate timestamps/session boundaries;
7. implement raw-data storage;
8. implement market/stock/relative feature primitives;
9. add anti-lookahead tests;
10. build the first deterministic cross-sectional backtest;
11. only then begin ML.

---

# 21. Non-Goals for the First Version

Do not spend early development time on:

- reinforcement learning,
- end-to-end Transformer price prediction,
- LLM-generated trading decisions,
- complex GNN architectures,
- options strategies,
- ultra-low-latency HFT,
- tick-level execution optimization,
- large dashboards,
- premature distributed infrastructure.

The first objective is to establish a correct, reproducible research loop.

---

# 22. Definition of a Valid Research Loop

A research idea is complete only when the project can perform:

```text
hypothesis
  ↓
data snapshot
  ↓
features
  ↓
labels
  ↓
training
  ↓
chronological validation
  ↓
cost-aware backtest
  ↓
diagnostics
  ↓
documented conclusion
```

A strategy should not be promoted because of one attractive equity curve.

Always inspect:

- stability across time,
- stability across regimes,
- stability across sectors,
- turnover/cost sensitivity,
- parameter sensitivity,
- out-of-sample behavior.

---

# 23. Ultimate System Target

The long-term target is:

```text
Market State
    ↓
Regime Probability
    ↓
Dynamic Peer Context
    ↓
Cross-sectional Alpha
    ↓
Long/Short Expected Value
    ↓
Meta-label / No-trade Filter
    ↓
Microstructure Entry Timing
    ↓
Risk Gate
    ↓
Execution
    ↓
Post-trade Attribution
```

Every new subsystem should improve one clearly identified layer of this pipeline rather than increasing complexity without measurable value.
