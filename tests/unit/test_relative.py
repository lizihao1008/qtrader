"""Market/sector-relative features and cross-sectional transforms."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.features.ranks import cross_sectional_rank, cross_sectional_zscore
from qtrader.features.relative import (
    align_reference,
    bar_log_returns,
    relative_returns,
    residual_returns,
    rolling_beta,
    trailing_return,
)
from tests.conftest import make_panel, minute_index


def test_overnight_gap_is_excluded_from_intraday_returns():
    day_one = [100.0, 101.0]
    day_two = [150.0, 151.0]
    index = minute_index(2).append(minute_index(2) + pd.Timedelta(days=1))
    close = pd.DataFrame({"AAA": day_one + day_two}, index=index)

    within = bar_log_returns(close, within_session=True)["AAA"]
    across = bar_log_returns(close, within_session=False)["AAA"]

    assert within.iloc[2] == 0.0  # the 100 -> 150 gap is not an intraday move
    assert across.iloc[2] == pytest.approx(np.log(150 / 101))
    assert within.iloc[3] == pytest.approx(np.log(151 / 150))


def test_trailing_return_sums_log_returns_exactly():
    close = pd.DataFrame({"AAA": [100.0, 110.0, 121.0, 133.1]}, index=minute_index(4))
    returns = bar_log_returns(close)
    assert trailing_return(returns, 2).iloc[2, 0] == pytest.approx(np.log(121 / 100))


def test_beta_of_one_when_the_symbol_tracks_its_reference():
    rng = np.random.default_rng(0)
    market = rng.normal(0, 0.001, 200)
    index = minute_index(200)
    returns = pd.DataFrame({"AAA": market * 2.0}, index=index)
    reference = pd.DataFrame({"AAA": market}, index=index)

    beta = rolling_beta(returns, reference, 60)["AAA"].dropna()
    assert beta.iloc[-1] == pytest.approx(2.0, rel=1e-6)


def test_residual_removes_the_reference_move():
    index = minute_index(3)
    returns = pd.DataFrame({"AAA": [0.004, 0.002, 0.000]}, index=index)
    reference = pd.DataFrame({"AAA": [0.002, 0.002, 0.002]}, index=index)
    beta = pd.DataFrame({"AAA": [1.0, 1.0, 1.0]}, index=index)

    residual = residual_returns(returns, reference, beta)["AAA"]
    assert residual.tolist() == pytest.approx([0.002, 0.0, -0.002])
    # With beta fixed at 1, the residual is just the difference.
    pd.testing.assert_series_equal(residual, relative_returns(returns, reference)["AAA"])


def test_align_reference_maps_each_symbol_to_its_own_sector():
    panel = make_panel({"AAA": [10.0, 11.0], "BBB": [20.0, 21.0], "XLK": [30.0, 31.0]})
    returns = bar_log_returns(panel.close)
    aligned = align_reference(returns, {"AAA": "XLK", "BBB": "XLK"})

    assert list(aligned.columns) == ["AAA", "BBB"]
    pd.testing.assert_series_equal(aligned["AAA"], returns["XLK"], check_names=False)


def test_align_reference_rejects_a_missing_reference():
    returns = bar_log_returns(make_panel({"AAA": [10.0, 11.0]}).close)
    with pytest.raises(KeyError, match="XLK"):
        align_reference(returns, {"AAA": "XLK"})


def test_cross_sectional_transforms_ignore_ineligible_symbols():
    index = minute_index(1)
    values = pd.DataFrame({"AAA": [1.0], "BBB": [2.0], "CCC": [99.0]}, index=index)
    eligible = pd.DataFrame({"AAA": [True], "BBB": [True], "CCC": [False]}, index=index)

    ranks = cross_sectional_rank(values, eligible)
    assert ranks.loc[index[0], "AAA"] == 0.5  # bottom of a two-name cross-section
    assert ranks.loc[index[0], "BBB"] == 1.0
    assert pd.isna(ranks.loc[index[0], "CCC"])

    scores = cross_sectional_zscore(values, eligible)
    assert scores.loc[index[0], "AAA"] == pytest.approx(-scores.loc[index[0], "BBB"])
    assert pd.isna(scores.loc[index[0], "CCC"])  # the outlier never enters the mean


def test_zscore_is_undefined_when_the_cross_section_has_no_dispersion():
    index = minute_index(1)
    values = pd.DataFrame({"AAA": [5.0], "BBB": [5.0]}, index=index)
    eligible = pd.DataFrame(True, index=index, columns=["AAA", "BBB"])
    assert cross_sectional_zscore(values, eligible).isna().all(axis=None)
