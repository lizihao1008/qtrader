"""The one-call diagnosis, and its promise: it works on a strategy it has never seen."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.analysis import Episodes, diagnose
from qtrader.experiments.splits import Split


def test_a_config_goes_straight_to_a_diagnosis(config):
    diagnosis = diagnose(config)

    assert len(diagnosis.episodes) == len(diagnosis.run.result.trades)
    assert not diagnosis.conditions.empty
    assert not diagnosis.contrast.empty
    assert diagnosis.threshold > 1.96  # more than one feature was screened


def test_the_same_call_works_on_a_different_strategy_unchanged(config):
    """The reuse promise: swapping the strategy needs no analysis-side change."""
    cross_sectional = diagnose(config)
    moving_average = diagnose(_as_ma_cross(config))

    assert len(moving_average.episodes) > 0
    # Each strategy's own view is picked up from its `setup_features`...
    assert "score" in cross_sectional.episodes.setup_columns
    assert "ma_gap_bps" in moving_average.episodes.setup_columns
    assert "ma_gap_bps" not in cross_sectional.episodes.setup_columns
    # ...on top of the same universal features for both.
    universal = {"stock_vol_bps", "market_vol_bps"}
    assert universal <= set(cross_sectional.episodes.setup_columns)
    assert universal <= set(moving_average.episodes.setup_columns)


def _as_ma_cross(config):
    from dataclasses import replace

    return replace(
        config,
        run_id=f"{config.run_id}__ma_cross",
        strategy=replace(
            config.strategy,
            name="ma_cross",
            params={"fast": 5, "slow": 20, "flat_time": "15:50"},
        ),
    )


def test_a_strategy_that_declares_nothing_is_still_screened(config):
    """No `setup_features` override is not a reason to skip the diagnosis."""
    diagnosis = diagnose(_as_ma_cross(config))
    universal = {
        "stock_vol_bps", "relative_volume", "vwap_distance_bps",
        "minute_of_session", "market_vol_bps",
    }
    assert universal <= set(diagnosis.episodes.setup_columns)


def test_the_summary_says_whether_anything_survived(config):
    diagnosis = diagnose(config)
    summary = diagnosis.summary()

    assert str(len(diagnosis.episodes)) in summary
    assert f"{diagnosis.threshold:.2f}" in summary
    if diagnosis.survivors.empty:
        assert "no feature clears the bar" in summary
    else:
        assert "CANDIDATE" in summary


def test_survivors_are_the_features_above_the_multiple_testing_bar(config):
    diagnosis = diagnose(config)
    assert (diagnosis.survivors["t_rho"].abs() > diagnosis.threshold).all()


def test_a_split_narrows_the_window_and_is_carried_through(config):
    split = Split(name="half", start="2026-08-03", end="2026-08-05", purpose="a test")
    diagnosis = diagnose(config, split=split)

    assert diagnosis.split is split
    assert "half" in diagnosis.run.config.run_id
    assert diagnosis.episodes.features["entry_time"].max() < pd.Timestamp(
        "2026-08-05", tz="UTC"
    )


def test_an_executed_run_can_be_reused_instead_of_rerun(config):
    from qtrader.runner import execute

    run = execute(config)
    diagnosis = diagnose(config, run=run)
    assert diagnosis.run is run


def test_artifacts_land_together_and_reload(config, tmp_path):
    diagnosis = diagnose(config)
    directory = diagnosis.save(tmp_path / "episodes")
    report = diagnosis.write_report(tmp_path / "episodes" / "episodes.html")

    assert {p.name for p in directory.iterdir()} >= {
        "episode_features.parquet", "episode_bars.parquet", "episodes.json",
    }
    reloaded = Episodes.load(directory)
    assert reloaded.setup_columns == diagnosis.episodes.setup_columns

    html = report.read_text()
    assert "Conditions vs gross return" in html
    assert "Episode gallery" in html


def test_an_unknown_split_fails_loudly(config):
    with pytest.raises(KeyError, match="unknown split"):
        diagnose(config, split="does_not_exist")
