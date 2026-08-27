"""Conditional screening: it must find a planted effect and reject noise."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.analysis.conditions import (
    bonferroni_t_threshold,
    outcome_contrast,
    quantile_profile,
    rank_conditions,
)


def synthetic(n: int = 600, effect: float = 8.0) -> pd.DataFrame:
    """`good` predicts the outcome; `noise` does not."""
    rng = np.random.default_rng(3)
    good = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    target = effect * good + rng.normal(0, 10, n)
    return pd.DataFrame(
        {
            "good": good,
            "noise": noise,
            "gross_return_bps": target,
            "gross_win": target > 0,
        }
    )


def test_a_planted_effect_ranks_first_and_clears_the_threshold():
    table = rank_conditions(synthetic(), ("good", "noise")).set_index("feature")
    threshold = bonferroni_t_threshold(2)

    assert abs(table.loc["good", "t_rho"]) > threshold
    assert abs(table.loc["noise", "t_rho"]) < threshold
    assert table.index[0] == "good"


def test_a_planted_effect_is_monotone_across_bins():
    profile = quantile_profile(synthetic(), "good")
    assert profile["mean_target"].is_monotonic_increasing
    assert profile["n"].sum() == 600


def test_pure_noise_produces_no_effect():
    rng = np.random.default_rng(9)
    features = pd.DataFrame(
        {
            "noise": rng.normal(0, 1, 400),
            "gross_return_bps": rng.normal(0, 10, 400),
        }
    )
    features["gross_win"] = features["gross_return_bps"] > 0
    assert abs(rank_conditions(features, ("noise",))["t_rho"].iloc[0]) < 2.0


def test_the_threshold_rises_with_the_number_of_features_screened():
    assert bonferroni_t_threshold(1) < bonferroni_t_threshold(16) < bonferroni_t_threshold(100)
    assert bonferroni_t_threshold(1) == pytest.approx(1.96, abs=0.01)


def test_outcome_contrast_separates_winners_from_losers():
    table = outcome_contrast(synthetic(), ("good", "noise")).set_index("feature")
    assert table.loc["good", "difference"] > 0  # winners had higher `good`
    assert abs(table.loc["good", "t_stat"]) > abs(table.loc["noise", "t_stat"])
    assert table.loc["good", "n_win"] + table.loc["good", "n_loss"] == 600


def test_features_with_too_few_observations_are_skipped():
    tiny = synthetic(n=10)
    assert rank_conditions(tiny, ("good",)).empty
