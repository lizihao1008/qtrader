"""End-to-end: stored bars -> config -> universe -> strategy -> engine -> saved run.

Uses synthetic bars written straight into the store, so the test never touches
the Alpaca API or the network.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from qtrader.config import RunConfig
from qtrader.data.ingest import build_clean, load_panel
from qtrader.data.schema import coerce_bars
from qtrader.data.storage import BarStore, DatasetKey, RawDataConflict
from qtrader.experiments import save_run
from qtrader.runner import execute
from qtrader.universe import Universe

SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
REFERENCES = ["SPY", "XLK", "XLF"]
SECTORS = {"AAA": "XLK", "BBB": "XLK", "CCC": "XLK", "DDD": "XLF", "EEE": "XLF", "FFF": "XLF"}

UNIVERSE_YAML = """
name: test_universe
benchmark: SPY
symbols: [AAA, BBB, CCC, DDD, EEE, FFF]
sectors: {AAA: XLK, BBB: XLK, CCC: XLK, DDD: XLF, EEE: XLF, FFF: XLF}
"""

CONFIG_TEMPLATE = """
run_id: pipeline_test
universe: {universe_path}
data:
  timeframe: 1Min
  feed: iex
  start: "2026-08-03"
  end: "2026-08-08"
strategy:
  name: cross_sectional_residual
  params:
    lookback: 10
    beta_window: 30
    n_positions: 2
    min_abs_zscore: 0.2
    rebalance_bars: 10
    min_eligible: 4
    flat_time: "15:50"
liquidity:
  min_price: 1.0
  min_dollar_volume: 0.0
  lookback_bars: 10
  max_stale_bars: 5
costs:
  half_spread_bps: 1.0
  slippage_bps: 0.5
execution:
  initial_cash: 50000.0
data_root: {data_root}
results_root: {results_root}
report:
  symbols: [AAA]
"""


def synthetic_bars(seed: int, n_sessions: int = 3, bars_per_session: int = 390) -> pd.DataFrame:
    """A reproducible random walk across several regular sessions."""
    rng = np.random.default_rng(seed)
    frames = []
    for day in range(n_sessions):
        start = pd.Timestamp("2026-08-03 09:30", tz="America/New_York") + pd.Timedelta(days=day)
        index = pd.date_range(start, periods=bars_per_session, freq="1min").tz_convert("UTC")
        close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0006, bars_per_session)))
        open_ = np.concatenate([[close[0]], close[:-1]])
        frames.append(
            pd.DataFrame(
                {
                    "open": open_,
                    "high": np.maximum(open_, close) * 1.0002,
                    "low": np.minimum(open_, close) * 0.9998,
                    "close": close,
                    "volume": rng.integers(1_000, 20_000, bars_per_session).astype(float),
                },
                index=index,
            )
        )
    return coerce_bars(pd.concat(frames))


@pytest.fixture
def store(tmp_path) -> BarStore:
    store = BarStore(tmp_path / "data")
    for seed, symbol in enumerate(SYMBOLS + REFERENCES):
        key = DatasetKey(symbol=symbol, timeframe="1Min", feed="iex")
        store.write_raw(key, synthetic_bars(seed))
        build_clean(key, store)
    return store


@pytest.fixture
def config(tmp_path, store) -> RunConfig:
    universe_path = tmp_path / "universe.yaml"
    universe_path.write_text(UNIVERSE_YAML)
    config_path = tmp_path / "run.yaml"
    config_path.write_text(
        CONFIG_TEMPLATE.format(
            universe_path=universe_path,
            data_root=store.root,
            results_root=tmp_path / "results",
        )
    )
    return RunConfig.from_yaml(config_path)


# ------------------------------------------------------------------ data layer
def test_raw_layer_is_immutable(store):
    key = DatasetKey(symbol="AAA", timeframe="1Min", feed="iex")
    tampered = store.read(key, "raw")
    tampered.iloc[0, tampered.columns.get_loc("close")] += 5.0
    with pytest.raises(RawDataConflict):
        store.write_raw(key, tampered)


def test_reingesting_the_same_window_is_idempotent(store):
    key = DatasetKey(symbol="AAA", timeframe="1Min", feed="iex")
    before = store.read(key, "raw")
    store.write_raw(key, before)
    pd.testing.assert_frame_equal(before, store.read(key, "raw"))


def test_panel_loads_every_universe_symbol(store, tmp_path):
    universe_path = tmp_path / "universe.yaml"
    universe_path.write_text(UNIVERSE_YAML)
    universe = Universe.from_yaml(universe_path)

    panel = load_panel(universe.all_symbols, store=store)
    assert set(panel.symbols) == set(universe.all_symbols)
    assert panel.close.notna().all(axis=None)


# --------------------------------------------------------------------- the run
def test_full_run_produces_all_artifacts(config):
    run = execute(config)
    out_dir = save_run(
        run.result,
        config,
        universe=run.context.universe,
        strategy_description=run.strategy.describe(),
    )

    for name in ("manifest.json", "metrics.json", "equity_curve.csv", "trades.csv",
                 "fills.csv", "weights.csv", "report.html"):
        assert (out_dir / name).exists(), name

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["config"]["strategy"]["params"]["lookback"] == 10
    assert manifest["universe"]["benchmark"] == "SPY"
    assert manifest["data_range"]["n_symbols"] == len(SYMBOLS) + len(REFERENCES)

    report = (out_dir / "report.html").read_text()
    assert "BUY fill" in report
    assert "Cumulative return" in report
    assert "Signal quality" in report


def test_the_strategy_takes_both_sides_and_stays_roughly_neutral(config):
    result = execute(config).result
    assert result.metrics["n_trades"] > 0
    assert set(result.trades["direction"]) == {"LONG", "SHORT"}
    assert abs(result.metrics["avg_net_exposure"]) < 0.1
    assert result.metrics["avg_gross_exposure"] > 0.1


def test_reference_etfs_are_never_traded(config):
    result = execute(config).result
    assert set(result.fills["symbol"]) <= set(SYMBOLS)


def test_rank_ic_is_reported_at_every_horizon(config):
    result = execute(config).result
    summary = result.metrics["rank_ic"]
    assert set(summary) == {"5b", "15b", "30b"}
    for stats in summary.values():
        assert stats["n_obs"] > 0
        assert -1.0 <= stats["mean_ic"] <= 1.0


def test_net_pnl_matches_the_equity_curve(config):
    """Trade accounting and mark-to-market equity must agree when flat."""
    result = execute(config).result

    assert result.equity_curve["n_positions"].iloc[-1] == 0  # flat at the end of the run
    realised = result.trades["net_pnl"].sum()
    equity_change = result.equity_curve["equity"].iloc[-1] - result.equity_curve["equity"].iloc[0]
    assert realised == pytest.approx(equity_change, rel=1e-9, abs=1e-6)
