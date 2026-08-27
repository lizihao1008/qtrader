"""Shared fixtures for the integration tests.

Synthetic bars are written straight into a temporary store, so integration
tests exercise the real pipeline without ever touching the Alpaca API or the
network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.config import RunConfig
from qtrader.data.ingest import build_clean
from qtrader.data.schema import coerce_bars
from qtrader.data.storage import BarStore, DatasetKey

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


