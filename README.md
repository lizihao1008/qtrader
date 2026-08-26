# qtrader

Intraday quantitative trading research system built on Alpaca market data.

The long-term design is in [`quant_intraday_project_outline.md`](quant_intraday_project_outline.md);
the working rules for development are in [`CLAUDE.md`](CLAUDE.md).

## Status

**M1 complete.** The pipeline runs end to end for a universe of stocks: Alpaca
1-minute bars → validation and storage → aligned panel → time-aware tradability
filter → market/sector-relative residual features → cross-sectional ranking →
cost-aware portfolio backtest → HTML report with K-line buy/sell markers, the
return curve, exposure, attribution, and rank-IC signal diagnostics.

Two strategies exist, both deterministic (roadmap stage V0):

| strategy | what it does |
| --- | --- |
| `ma_cross` | single-name MA crossover; the baseline that validates the plumbing |
| `cross_sectional_residual` | ranks the universe by sector-residual return and holds the extremes long/short |

The cross-sectional strategy **does not survive out-of-sample testing** — see
[`docs/progress/PROGRESS.md`](docs/progress/PROGRESS.md) for the numbers. That is
a result, not a bug: the research loop is doing its job.

Machine learning has deliberately not started; M2 (LightGBM ranking) is next.

## Setup

```bash
conda activate quant
pip install -e ".[dev]"
export ALPACA_API_KEY=...      # never commit these
export ALPACA_SECRET_KEY=...
```

## Run a backtest

```bash
python scripts/download_data.py --config config/backtest/xsec_reversion.yaml
python scripts/run_backtest.py  --config config/backtest/xsec_reversion.yaml --open
```

Results land in `results/<run_id>/`:

| file | contents |
| --- | --- |
| `report.html` | return curve vs benchmark, exposure, K-lines with every fill marked, rank IC, attribution, trade log |
| `metrics.json` | Sharpe, drawdown, hit rate, turnover, costs, rank IC by horizon |
| `equity_curve.csv` | per-bar equity, gross/net exposure, position count, drawdown |
| `trades.csv` / `fills.csv` | round trips and individual executions |
| `weights.csv` | what the strategy asked for, at every bar the book changed |
| `manifest.json` | config + universe + git commit + data range (reproducibility record) |

## Check parameter sensitivity

Never accept a result from one setting:

```bash
python scripts/sweep.py --config config/backtest/xsec_reversion.yaml \
    --grid lookback=30,60,120 --grid rebalance_bars=60,120,240
```

## Pipeline

```text
Alpaca 1-min bars
  -> qtrader.data       validate (schema, OHLC sanity, timestamps) -> raw -> clean
                        BarPanel aligns every symbol on one timestamp grid
  -> qtrader.universe   who is in the universe, and what was tradable at each bar
  -> qtrader.features   causal per-symbol indicators, residual returns, cross-sectional ranks
  -> qtrader.strategies target weights per symbol per bar, sum(|w|) <= 1
  -> qtrader.backtest   signal at bar t close -> fill at bar t+1 open, net of costs
                        + rank IC against forward returns
  -> qtrader.viz        K-lines with trade markers, return curve, exposure
  -> results/<run_id>/
```

## Adding a strategy

1. subclass `Strategy` in `src/qtrader/strategies/`; take a `MarketContext`,
   return a `StrategySignals` of target weights (and `scores`, if it ranks);
2. register it in `strategies/registry.py`;
3. copy a config in `config/backtest/`, point `strategy.name` at it;
4. add tests under `tests/unit/`.

The panel, tradability filter, engine, costs, metrics and reporting are reused
unchanged.

## Tests

```bash
pytest
```
