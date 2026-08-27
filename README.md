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
| `trend_ratchet` | enters on a MACD turn confirmed by a calibrated trend test, exits through a monotone volatility barrier (stop, then trail) |

Neither alpha strategy survives out-of-sample testing —
[R01](docs/research/R01-conviction-and-reversion.md) and
[R02](docs/research/R02-trend-ratchet.md) have the numbers. That is a result,
not a bug: the research loop is doing its job. `trend_ratchet`'s risk machinery
*does* work as specified, and it reduces the open question to one number — the
entry signal needs a 41.3% hit rate at its 1.42 payoff ratio, and delivers 39%.

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
python scripts/sweep.py --config config/backtest/xsec_reversion.yaml --split mine --grid lookback=30,60,120
```

## Ask why the trades worked

Collect the K-line window around every round trip, screen what separated wins
from losses, and save both so the analysis can be redone without a backtest.
**This works on any strategy with no analysis-side changes:**

```bash
python scripts/analyze_episodes.py --config config/backtest/<any-config>.yaml --split mine
```

or from Python:

```python
from qtrader.analysis import diagnose

diagnosis = diagnose("config/backtest/my_new_strategy.yaml", split="mine")
print(diagnosis.summary())        # did anything clear the multiple-testing bar?
diagnosis.conditions              # every setup feature ranked against gross return
diagnosis.contrast                # winners vs losers, feature by feature
diagnosis.save()                  # parquet: per-trade features + K-line windows
diagnosis.write_report()          # paths, screens, best/worst candlestick gallery
```

Every run is screened against the same universal market features — volatility,
relative volume, VWAP distance, breadth, market state, time of day. A strategy
that wants its own quantities screened too overrides one method:

```python
class MyStrategy(Strategy):
    def setup_features(self, signals, context):
        return {"my_signal_z": signals.stack("signal_z")}   # scale-free, causal
```

Writes `results/<run_id>/episodes/`: `episode_features.parquet` (setup +
outcome per trade), `episode_bars.parquet` (the K-lines), `episodes.json`
(which columns are the setup), and `episodes.html`. The first findings are in
[`docs/research/R01`](docs/research/R01-conviction-and-reversion.md).

## Evaluation windows

`config/splits.yaml` names contiguous windows and what each may be used for —
`mine` generates hypotheses, `validate` confirms one and is then spent, `burned`
is the window that chose the current parameters and cannot test them. Pass
`--split` to any script. See [ADR-0004](docs/adr/ADR-0004-chronological-splits.md).

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
  -> qtrader.analysis   one episode per round trip: K-line window + setup + outcome
  -> qtrader.viz        K-lines with trade markers, return curve, exposure, episodes
  -> results/<run_id>/
```

## Adding a strategy

1. subclass `Strategy` in `src/qtrader/strategies/`; take a `MarketContext`,
   return a `StrategySignals` of target weights (and `scores`, if it ranks);
2. register it in `strategies/registry.py`;
3. copy a config in `config/backtest/`, point `strategy.name` at it;
4. optionally override `setup_features` to have your own quantities screened;
5. add tests under `tests/unit/`.

The panel, tradability filter, engine, costs, metrics, reporting **and the
win/loss diagnosis** are reused unchanged.

## Tests

```bash
pytest
```
