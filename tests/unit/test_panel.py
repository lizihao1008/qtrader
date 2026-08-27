"""Panel alignment — the grid every cross-sectional decision is made on."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.data.panel import BarPanel
from tests.conftest import make_bars, make_panel, minute_index


def test_symbols_are_aligned_on_the_union_index():
    full = make_bars([10.0, 11.0, 12.0, 13.0])
    partial = make_bars([20.0, 21.0, 22.0, 23.0]).drop(index=[minute_index(4)[1]])

    panel = BarPanel.from_frames({"AAA": full, "BBB": partial})
    assert list(panel.index) == list(full.index)
    assert panel.symbols == ("AAA", "BBB")


def test_untraded_bars_hold_the_last_price_and_report_zero_volume():
    full = make_bars([10.0, 11.0, 12.0])
    gapped = make_bars([20.0, 21.0, 22.0]).drop(index=[minute_index(3)[1]])

    panel = BarPanel.from_frames({"AAA": full, "BBB": gapped})
    assert panel.close["BBB"].iloc[1] == 20.0  # carried forward, not interpolated
    assert panel.volume["BBB"].iloc[1] == 0.0
    assert panel.field("high")["BBB"].iloc[1] == 20.0  # no range in an untraded minute


def test_traded_and_available_masks_differ():
    full = make_bars([10.0, 11.0, 12.0])
    gapped = make_bars([20.0, 21.0, 22.0]).drop(index=[minute_index(3)[1]])

    panel = BarPanel.from_frames({"AAA": full, "BBB": gapped})
    assert not panel.traded["BBB"].iloc[1]  # nothing printed
    assert panel.available["BBB"].iloc[1]  # but a price is known


def test_forward_fill_does_not_cross_a_session_boundary():
    day_one = make_bars([10.0, 11.0])
    day_two = make_bars([30.0, 31.0])
    day_two.index = day_two.index + pd.Timedelta(days=1)

    late_starter = pd.concat([day_one, day_two.iloc[[1]]])
    panel = BarPanel.from_frames(
        {"AAA": pd.concat([day_one, day_two]), "BBB": late_starter}
    )
    # BBB has no print at day 2's open: it must be unavailable, not stuck at 11.
    assert pd.isna(panel.close["BBB"].iloc[2])
    assert not panel.available["BBB"].iloc[2]


def test_subset_keeps_the_index_and_bars_round_trip():
    panel = make_panel({"AAA": [10.0, 11.0], "BBB": [20.0, 21.0]})
    only_a = panel.subset(["AAA"])
    assert only_a.symbols == ("AAA",)
    assert list(only_a.index) == list(panel.index)

    bars = panel.bars("BBB")
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert bars["close"].tolist() == [20.0, 21.0]


def test_replacing_a_field_leaves_the_original_untouched():
    """Audits rewrite history on a copy; the original panel must not move."""
    panel = make_panel({"AAA": [10.0, 11.0], "BBB": [20.0, 21.0]})
    doubled = panel.replace_field("close", panel.close * 2)

    assert doubled.close["AAA"].tolist() == [20.0, 22.0]
    assert panel.close["AAA"].tolist() == [10.0, 11.0]
    assert list(doubled.symbols) == list(panel.symbols)
    with pytest.raises(KeyError):
        panel.replace_field("nonexistent", panel.close)
