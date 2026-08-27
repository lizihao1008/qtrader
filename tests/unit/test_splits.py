"""Chronological split handling."""

from __future__ import annotations

import pytest

from qtrader.config import RunConfig
from qtrader.experiments.splits import apply_split, load_splits

SPLITS_YAML = """
splits:
  mine:
    start: "2026-02-02"
    end: "2026-06-01"
    purpose: hypothesis generation
  validate:
    start: "2026-06-01"
    end: "2026-07-28"
"""

CONFIG_YAML = """
run_id: demo
universe: config/universe/us_liquid_22.yaml
data:
  start: "2026-01-01"
  end: "2026-12-31"
strategy:
  name: ma_cross
  params: {fast: 5, slow: 20}
"""


@pytest.fixture
def config(tmp_path) -> RunConfig:
    path = tmp_path / "run.yaml"
    path.write_text(CONFIG_YAML)
    return RunConfig.from_yaml(path)


@pytest.fixture
def splits(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text(SPLITS_YAML)
    return load_splits(path)


def test_splits_are_contiguous_and_ordered(splits):
    assert splits["mine"].end == splits["validate"].start
    assert splits["mine"].start < splits["mine"].end


def test_applying_a_split_narrows_the_window_and_renames_the_run(config, splits):
    narrowed = apply_split(config, splits["mine"])

    assert (narrowed.data.start, narrowed.data.end) == ("2026-02-02", "2026-06-01")
    assert narrowed.run_id == "demo__mine"
    # The original is untouched, so two splits can be run from one config.
    assert config.data.start == "2026-01-01"


def test_different_splits_write_to_different_directories(config, splits):
    mine = apply_split(config, splits["mine"]).output_dir()
    validate = apply_split(config, splits["validate"]).output_dir()
    assert mine != validate


def test_report_options_survive_config_parsing(tmp_path):
    from qtrader.config import RunConfig

    path = tmp_path / "run.yaml"
    path.write_text(CONFIG_YAML + "report:\n  symbols: [AAA]\n  max_candles: 390\n")
    config = RunConfig.from_yaml(path)

    assert config.report_symbols == ("AAA",)
    assert config.report_max_candles == 390
    assert config.to_dict()["report_max_candles"] == 390


def test_the_purpose_of_a_split_is_carried_with_it(splits):
    assert splits["mine"].purpose == "hypothesis generation"
    assert "mine" in splits["mine"].describe()
