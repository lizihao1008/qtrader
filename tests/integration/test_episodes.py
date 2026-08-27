"""Episode extraction: the K-line windows and setup features behind each trade."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.analysis import (
    Episodes,
    excursion_summary,
    extract_episodes,
    oriented_paths,
)
from qtrader.runner import execute

CONTEXT_BARS = 30


@pytest.fixture
def episodes(config) -> Episodes:
    return extract_episodes(execute(config), context_bars=CONTEXT_BARS)


def test_one_episode_per_round_trip_with_unique_ids(config, episodes):
    trades = execute(config).result.trades
    assert len(episodes) == len(trades)
    assert episodes.features.index.is_unique


def test_each_window_covers_the_setup_and_the_holding_period(episodes):
    for episode_id in episodes.features.index[:5]:
        window = episodes.window(episode_id)
        meta = episodes.features.loc[episode_id]

        assert window["offset"].min() <= 0
        assert window["offset"].max() == meta["hold_bars"]
        assert (window["offset"].min() >= -CONTEXT_BARS)
        assert window.index.is_monotonic_increasing


def test_returns_are_consistent_with_the_trade_ledger(config, episodes):
    trades = execute(config).result.trades
    features = episodes.features

    assert features["cost_bps"].gt(0).all()
    # Net is gross minus costs, per episode, in the same units.
    assert np.allclose(
        features["net_return_bps"], features["gross_return_bps"] - features["cost_bps"]
    )
    assert features["gross_win"].sum() == int((trades["gross_pnl"] > 0).sum())


def test_excursions_bracket_the_realised_return(episodes):
    features = episodes.features
    assert (features["mfe_bps"] >= features["gross_return_bps"] - 1e-6).all()
    assert (features["mae_bps"] <= features["gross_return_bps"] + 1e-6).all()


def test_setup_features_are_present_and_finite_enough_to_analyse(episodes):
    features = episodes.features
    assert episodes.setup_columns
    for column in episodes.setup_columns:
        assert column in features.columns, column
    # The score is what the strategy ranked on; it must exist for every trade.
    assert features["abs_score"].notna().all()
    assert (features["abs_score"] >= 0).all()


def test_setup_features_are_measured_before_the_fill(config):
    """The decision bar, not the fill bar — otherwise the context is not causal."""
    run = execute(config)
    episodes = extract_episodes(run, context_bars=CONTEXT_BARS)
    lag = run.config.execution.execution_lag_bars
    index = run.context.panel.index
    scores = run.result.signals.scores

    for episode_id in episodes.features.index[:5]:
        meta = episodes.features.loc[episode_id]
        entry_position = index.get_loc(meta["entry_time"])
        expected = scores.iat[entry_position - lag, scores.columns.get_loc(meta["symbol"])]
        assert meta["score"] == pytest.approx(expected, nan_ok=True)


def test_paths_are_oriented_so_up_is_profit(episodes):
    paths = oriented_paths(episodes)
    at_entry = paths.loc[paths["offset"] == 0, "path_bps"]
    assert at_entry.abs().max() < 500  # entry bar is near its own reference

    final = paths.sort_values("offset").groupby("episode_id")["path_bps"].last()
    winners = episodes.features["gross_win"]
    # Winners end above their entry once flipped for direction; losers below.
    assert final[winners[winners].index].mean() > final[winners[~winners].index].mean()


def test_excursion_summary_reports_both_groups(episodes):
    summary = excursion_summary(episodes).set_index("group")
    assert set(summary.index) == {"gross winners", "gross losers"}
    assert summary.loc["gross winners", "mean_gross_bps"] > 0
    assert summary.loc["gross losers", "mean_gross_bps"] < 0


def test_episodes_round_trip_through_disk(episodes, tmp_path):
    directory = episodes.save(tmp_path / "episodes")
    reloaded = Episodes.load(directory)

    pd.testing.assert_frame_equal(episodes.features, reloaded.features)
    pd.testing.assert_frame_equal(episodes.bars, reloaded.bars)
    # Column roles survive too, so a reloaded analysis screens the same columns.
    assert reloaded.setup_columns == episodes.setup_columns
    assert reloaded.context_bars == episodes.context_bars
