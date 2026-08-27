"""End-to-end: stored bars -> config -> universe -> strategy -> engine -> saved run."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from qtrader.data.ingest import load_panel
from qtrader.data.storage import DatasetKey, RawDataConflict
from qtrader.experiments import save_run
from qtrader.runner import execute
from qtrader.universe import Universe
from tests.integration.conftest import REFERENCES, SYMBOLS, UNIVERSE_YAML


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
