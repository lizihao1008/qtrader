"""The confirmation harness — with a stub model in place of Kronos.

The only thing worth testing here is the plumbing, and the one part of the
plumbing that can silently invalidate every result downstream is the window
boundary. If the context window includes a single bar from the forecast
horizon, the filter is reading the answer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.models.kronos_confirm import (
    Candidate,
    collect_candidates,
    load_scores,
    save_scores,
    score_candidates,
)
from tests.conftest import make_panel, minute_index

LOOKBACK = 8
PRED_LEN = 3


class RecordingPredictor:
    """Stands in for KronosPredictor; remembers what it was shown."""

    def __init__(self, drift: float = 0.01):
        self.drift = drift
        self.seen: list[pd.DataFrame] = []

    def predict_batch(self, *, df_list, x_timestamp_list, y_timestamp_list,
                      pred_len, **_):
        self.seen.extend(df_list)
        forecasts = []
        for frame in df_list:
            last = float(frame["close"].iloc[-1])
            path = [last * (1 + self.drift) ** (k + 1) for k in range(pred_len)]
            forecasts.append(pd.DataFrame({"close": path}))
        return forecasts


def setup(n: int = 40):
    prices = list(100.0 + np.cumsum(np.linspace(0.01, 0.02, n)))
    panel = make_panel({"AAA": prices, "BBB": prices})
    volatility = pd.DataFrame(0.001, index=panel.index, columns=["AAA", "BBB"])
    return panel, volatility


# ------------------------------------------------------------- candidate set
def test_candidates_are_only_the_bars_the_strategy_proposed():
    index = minute_index(20)
    indicators = {
        "AAA": pd.DataFrame({"candidate": [0.0] * 12 + [1.0] + [0.0] * 7}, index=index),
        "BBB": pd.DataFrame({"candidate": [0.0] * 15 + [-1.0] + [0.0] * 4}, index=index),
    }
    found = collect_candidates(indicators, index, lookback=8)

    assert [(c.position, c.symbol, c.direction) for c in found] == [
        (12, "AAA", 1),
        (15, "BBB", -1),
    ]


def test_candidates_without_a_full_context_window_are_dropped():
    index = minute_index(20)
    indicators = {"AAA": pd.DataFrame({"candidate": [0.0] * 3 + [1.0] + [0.0] * 16},
                                      index=index)}
    assert collect_candidates(indicators, index, lookback=8) == []


def test_indicators_without_a_candidate_column_are_ignored():
    index = minute_index(20)
    indicators = {"AAA": pd.DataFrame({"momentum_z": [1.0] * 20}, index=index)}
    assert collect_candidates(indicators, index, lookback=8) == []


# -------------------------------------------------------------- the boundary
def test_the_context_window_ends_at_the_candidate_bar_and_no_later():
    """The leakage test. The model must not see the bar it is predicting."""
    panel, volatility = setup()
    predictor = RecordingPredictor()
    at = 25

    score_candidates(
        [Candidate(at, "AAA", 1)], panel, volatility, predictor,
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )

    shown = predictor.seen[0]
    closes = panel.close["AAA"].to_numpy()
    assert len(shown) == LOOKBACK
    assert shown["close"].iloc[-1] == pytest.approx(closes[at])
    assert shown["close"].iloc[0] == pytest.approx(closes[at - LOOKBACK + 1])
    assert not np.isin(closes[at + 1 : at + 1 + PRED_LEN], shown["close"].to_numpy()).any()


def test_the_score_is_the_forecast_return_in_units_of_horizon_volatility():
    panel, volatility = setup()
    predictor = RecordingPredictor(drift=0.01)
    at = 25

    scores = score_candidates(
        [Candidate(at, "AAA", 1)], panel, volatility, predictor,
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )

    expected = np.log(1.01**PRED_LEN) / (0.001 * np.sqrt(PRED_LEN))
    assert scores["AAA"].iloc[at] == pytest.approx(expected, rel=1e-9)
    assert scores.drop(index=scores.index[at]).isna().all().all(), "only candidates scored"


def test_a_falling_forecast_scores_negative():
    panel, volatility = setup()
    scores = score_candidates(
        [Candidate(25, "AAA", 1)], panel, volatility, RecordingPredictor(drift=-0.01),
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )
    assert scores["AAA"].iloc[25] < 0


# ------------------------------------------------------------------ handling
def test_a_window_with_no_usable_history_is_skipped_not_invented():
    panel, volatility = setup()
    close = panel.close.copy()
    close.iloc[:30, close.columns.get_loc("AAA")] = np.nan
    panel = panel.replace_field("close", close)

    predictor = RecordingPredictor()
    scores = score_candidates(
        [Candidate(25, "AAA", 1)], panel, volatility, predictor,
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )
    assert predictor.seen == []
    assert scores["AAA"].isna().all()


def test_a_zero_volatility_bar_produces_no_score_rather_than_an_infinity():
    panel, volatility = setup()
    volatility.iloc[25, volatility.columns.get_loc("AAA")] = 0.0
    scores = score_candidates(
        [Candidate(25, "AAA", 1)], panel, volatility, RecordingPredictor(),
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )
    assert scores["AAA"].isna().all()


def test_an_empty_candidate_set_returns_an_empty_frame():
    panel, volatility = setup()
    scores = score_candidates([], panel, volatility, RecordingPredictor())
    assert scores.shape == (len(panel.index), len(panel.symbols))
    assert scores.isna().all().all()


def test_scores_round_trip_through_parquet(tmp_path):
    panel, volatility = setup()
    scores = score_candidates(
        [Candidate(25, "AAA", 1)], panel, volatility, RecordingPredictor(),
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )
    path = save_scores(scores, tmp_path / "scores.parquet")
    # Parquet does not carry the index freq; the strategy reindexes by
    # timestamp, so only the labels and values have to survive.
    pd.testing.assert_frame_equal(load_scores(path), scores, check_freq=False)


# ------------------------------------------------------------------- paths
def test_forecast_paths_are_stamped_with_the_bars_they_predict():
    """The path must start one bar *after* the candidate, never on it."""
    from qtrader.models.kronos_confirm import forecast_paths

    panel, _ = setup()
    at = 25
    paths = forecast_paths(
        [Candidate(at, "AAA", 1)], panel, RecordingPredictor(),
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )

    key = (panel.index[at], "AAA")
    assert list(paths) == [key]
    assert list(paths[key].index) == list(panel.index[at + 1 : at + 1 + PRED_LEN])


def test_forecast_paths_and_scores_agree_on_the_same_window():
    """Both entry points share one window builder; they must not drift apart."""
    from qtrader.models.kronos_confirm import forecast_paths

    panel, volatility = setup()
    at = 25
    predictor = RecordingPredictor()

    scores = score_candidates(
        [Candidate(at, "AAA", 1)], panel, volatility, predictor,
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )
    paths = forecast_paths(
        [Candidate(at, "AAA", 1)], panel, predictor,
        lookback=LOOKBACK, pred_len=PRED_LEN, batch_size=4,
    )

    last = float(panel.close["AAA"].iloc[at])
    target = float(paths[(panel.index[at], "AAA")]["close"].iloc[-1])
    expected = np.log(target / last) / (0.001 * np.sqrt(PRED_LEN))
    assert scores["AAA"].iloc[at] == pytest.approx(expected, rel=1e-9)
