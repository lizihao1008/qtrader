"""The market_state consolidation detector used as an entry gate.

These tests pin the adapter's contract — shape, causality of the grid carry,
and the choice to treat unconfirmed breaks as still inside the range. They do
not re-test the detector itself: its own repository owns that, including the
leakage suite that makes it worth importing rather than reimplementing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.experiments.consolidation_gate import in_range_mask, range_state

pytest.importorskip("market_state")

from qtrader.data.panel import BarPanel  # noqa: E402
from tests.conftest import make_bars  # noqa: E402

#: The detector's thresholds are stated in 5-minute bars, so the fixture is on
#: that grid; a 1-minute index would make every step look like a data gap.
STEP = "5min"


def _five_minute_index(n: int) -> pd.DatetimeIndex:
    start = pd.Timestamp("2026-08-03 09:30", tz="America/New_York")
    return pd.date_range(start, periods=n, freq=STEP).tz_convert("UTC")


def _flat_then_trending(n_flat: int = 40, n_trend: int = 20) -> list[float]:
    """Chop, then a straight run out of it.

    A range is narrow *relative to its own bar-to-bar movement*, so the flat
    half oscillates rather than drifting: the width stays 0.1 while each bar
    travels the full 0.1, which is what the width/ATR and efficiency tests are
    written to recognise.
    """
    chop = [100.0 + 0.1 * (i % 2) for i in range(n_flat)]
    trend = [chop[-1] + 0.1 * (i + 1) for i in range(n_trend)]
    return chop + trend


def _panel(path: list[float]):
    index = _five_minute_index(len(path))
    return BarPanel.from_frames(
        {symbol: make_bars(path, index=index) for symbol in ("SPY", "QQQ")}
    )


def test_the_mask_covers_the_panel_and_is_boolean():
    panel = _panel(_flat_then_trending())
    mask = in_range_mask(panel, ["SPY", "QQQ"])
    assert list(mask.index) == list(panel.index)
    assert set(mask.columns) == {"SPY", "QQQ"}
    assert mask.dtypes.eq(bool).all()


def test_a_directionless_stretch_is_marked_and_a_trend_is_not():
    panel = _panel(_flat_then_trending())
    mask = in_range_mask(panel, ["SPY"])["SPY"].to_numpy()
    assert mask.any(), "a flat stretch produced no range at all"
    assert not mask.all(), "a straight trend was still called a range"
    # The flat half must carry more range bars than the trending half.
    assert mask[:60].sum() > mask[60:].sum()


def test_the_mask_never_depends_on_bars_after_the_one_it_labels():
    """Truncation invariance, the same property market_state's own suite pins,
    checked here at the adapter boundary because that is what the gate reads."""
    path = _flat_then_trending()
    full = in_range_mask(_panel(path), ["SPY"])["SPY"]
    cut = 75
    prefix = in_range_mask(_panel(path[:cut]), ["SPY"])["SPY"]
    pd.testing.assert_series_equal(full.iloc[:cut], prefix, check_freq=False)


def test_carrying_the_mask_to_a_finer_grid_delays_it_by_one_coarse_bar():
    """Without the delay the gate would know a range had ended before the
    5-minute bar that ended it had closed."""
    coarse = pd.date_range("2026-06-01 13:30", periods=4, freq="5min", tz="UTC")
    fine = pd.date_range("2026-06-01 13:30", periods=20, freq="1min", tz="UTC")
    state = {"SPY": pd.DataFrame({"in_range": [True, True, False, False]}, index=coarse)}

    class _Panel:
        index = coarse

    import qtrader.experiments.consolidation_gate as gate

    original = gate.range_state
    gate.range_state = lambda *a, **k: state
    try:
        carried = gate.in_range_mask(_Panel(), ["SPY"], fine_index=fine)["SPY"]
    finally:
        gate.range_state = original

    # 13:30's value is unknown until 13:35, so the first five minutes are False.
    assert not carried.loc[:"2026-06-01 13:34"].any()
    assert carried.loc["2026-06-01 13:35":"2026-06-01 13:44"].all()
    assert not carried.loc["2026-06-01 13:45":].any()


def test_the_state_table_keeps_the_detector_s_own_columns():
    """The adapter must not rename or reinterpret what the detector emits;
    `range_status` distinguishes RANGE from a pending, unconfirmed break."""
    state = range_state(_panel(_flat_then_trending()), ["SPY"])["SPY"]
    for column in ("in_range", "range_high", "range_low", "range_status"):
        assert column in state


# ---------------------------------------------------- the persistence model


def _predictions(tmp_path, rows) -> str:
    """A stand-in for market_state's walk-forward predictions parquet."""
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.DatetimeIndex(frame["timestamp"])
    frame["decision_time"] = frame["timestamp"] + pd.Timedelta(minutes=5)
    path = tmp_path / "predictions.parquet"
    frame.to_parquet(path)
    return str(path)


def test_a_bar_with_no_box_is_not_a_veto(tmp_path):
    """NaN means "there is no range here", which is the opposite of "the range
    will hold". Filling it as a veto would silently gate every quiet symbol."""
    from qtrader.experiments.consolidation_gate import persistence_veto

    index = _five_minute_index(4)
    path = _predictions(tmp_path, [
        {"symbol": "SPY", "timestamp": index[1], "pred_persist": 0.9},
    ])
    veto = persistence_veto(index, ["SPY"], threshold=0.5, path=path)["SPY"]
    assert veto.tolist() == [False, True, False, False]


def test_the_threshold_selects_which_boxes_are_worth_standing_aside_for(tmp_path):
    from qtrader.experiments.consolidation_gate import persistence_veto

    index = _five_minute_index(3)
    path = _predictions(tmp_path, [
        {"symbol": "SPY", "timestamp": t, "pred_persist": p}
        for t, p in zip(index, (0.2, 0.5, 0.8))
    ])
    at_half = persistence_veto(index, ["SPY"], threshold=0.5, path=path)["SPY"]
    assert at_half.tolist() == [False, True, True]
    at_high = persistence_veto(index, ["SPY"], threshold=0.7, path=path)["SPY"]
    assert at_high.tolist() == [False, False, True]


def test_predictions_with_an_inconsistent_decision_lag_are_refused(tmp_path):
    """The frame is used on its own index with no shift, which is only correct
    while every row is knowable exactly when its own bar closes."""
    from qtrader.experiments.consolidation_gate import persistence_probability

    index = _five_minute_index(2)
    frame = pd.DataFrame({
        "symbol": ["SPY", "SPY"],
        "timestamp": index,
        "decision_time": [index[0] + pd.Timedelta(minutes=5), index[1]],
        "pred_persist": [0.4, 0.6],
    })
    path = tmp_path / "bad.parquet"
    frame.to_parquet(path)
    with pytest.raises(ValueError, match="uniform decision lag"):
        persistence_probability(index, ["SPY"], path=str(path))
