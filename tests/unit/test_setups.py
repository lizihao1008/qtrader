"""The setup gallery — and the two lookups that would silently draw nothing.

Both the watched level and the Kronos forecast belong to the **decision** bar,
one execution lag before the fill that an episode's `entry_time` records. Read
them at the fill instead and the panels still render, just without the two
things the chart exists to show. So both are asserted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.analysis.episodes import Episodes
from qtrader.viz.charts import _market_naive
from qtrader.viz.setups import KRONOS_COLOR, RANGE_COLOR, setup_gallery
from tests.conftest import minute_index


def local(position: int):
    """The fixture's bar at ``position``, as the chart's tz-naive local time."""
    return _market_naive(minute_index(CONTEXT + HOLD + 1))[position]

HOLD = 6
CONTEXT = 5
LEVEL = 101.0


def episodes(direction: str = "LONG", *, level: float = LEVEL) -> Episodes:
    """One episode: five bars of setup, an entry, six bars held."""
    n = CONTEXT + HOLD + 1
    index = minute_index(n)
    offset = np.arange(n) - CONTEXT
    close = 100.0 + offset * 0.1

    bars = pd.DataFrame(
        {
            "episode_id": "E1",
            "timestamp": index,
            "offset": offset,
            "open": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": 1_000.0,
            # The level and the zone exist only while the break is unconsumed,
            # which is up to and including the decision bar.
            "watched_level": np.where(offset <= -1, level, np.nan),
            "zone": np.where(offset <= -1, 0.05, np.nan),
            "previous_day_resistance": 100.4,
            "previous_day_support": 99.9,
        }
    )
    features = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "direction": [direction],
            "gross_return_bps": [42.0],
            "hold_bars": [HOLD],
            "entry_time": [index[CONTEXT]],
        },
        index=pd.Index(["E1"], name="episode_id"),
    )
    return Episodes(features=features, bars=bars, context_bars=CONTEXT)


def decision_time() -> pd.Timestamp:
    return minute_index(CONTEXT + HOLD + 1)[CONTEXT - 1]


def forecast(rising: bool = True) -> dict:
    step = 0.2 if rising else -0.2
    return {
        (decision_time(), "AAA"): pd.DataFrame(
            {"close": [99.9 + step * (k + 1) for k in range(4)]}
        )
    }


def traces(figure, name: str) -> list:
    return [t for t in figure.data if t.name == name]


# ------------------------------------------------------------------ contents
def test_the_panel_carries_candles_an_entry_and_an_exit():
    figure = setup_gallery(episodes(), ["E1"], title="t")
    assert any(t.type == "candlestick" for t in figure.data)
    assert traces(figure, "entry") and traces(figure, "exit")


def test_the_entry_marker_points_the_way_the_rule_predicted():
    up = traces(setup_gallery(episodes("LONG"), ["E1"], title="t"), "entry")[0]
    down = traces(setup_gallery(episodes("SHORT"), ["E1"], title="t"), "entry")[0]

    assert up.marker.symbol == "triangle-up"
    assert down.marker.symbol == "triangle-down"


def test_the_watched_level_is_drawn_from_the_decision_bar():
    """At the fill the break is already consumed and the level is NaN."""
    figure = setup_gallery(episodes(level=101.0), ["E1"], title="t")
    levels = [s for s in figure.layout.shapes if s.type == "line" and s.y0 == s.y1]
    assert 101.0 in {s.y0 for s in levels}


def test_the_tolerance_zone_is_shaded_around_the_level():
    figure = setup_gallery(episodes(), ["E1"], title="t")
    bands = [s for s in figure.layout.shapes if s.type == "rect"]
    assert bands, "no zone drawn"
    assert (bands[0].y0, bands[0].y1) == pytest.approx((LEVEL - 0.05, LEVEL + 0.05))


# ------------------------------------------------------------------ forecast
def test_the_forecast_is_looked_up_by_the_decision_bar_not_the_fill():
    figure = setup_gallery(episodes(), ["E1"], title="t", forecasts=forecast())
    assert traces(figure, "Kronos"), "forecast keyed by the fill would draw nothing"


def test_the_forecast_is_anchored_at_the_decision_bars_close():
    figure = setup_gallery(episodes(), ["E1"], title="t", forecasts=forecast())
    path = traces(figure, "Kronos")[0]

    assert path.x[0] == local(CONTEXT - 1), "the path must start at the bar the model last saw"
    assert path.y[0] == pytest.approx(99.9), "anchored on the decision close"
    assert path.x[1] == local(CONTEXT), "and continue onto real bars, not offsets"
    assert path.line.color == KRONOS_COLOR


def test_a_missing_forecast_leaves_the_panel_otherwise_intact():
    figure = setup_gallery(episodes(), ["E1"], title="t", forecasts={})
    assert not traces(figure, "Kronos")
    assert any(t.type == "candlestick" for t in figure.data)


# --------------------------------------------------------------------- title
def test_the_title_marks_whether_the_forecast_agreed():
    index = minute_index(CONTEXT + HOLD + 1)
    scores = pd.DataFrame({"AAA": np.nan}, index=index)
    scores.loc[decision_time(), "AAA"] = 0.75

    agreeing = setup_gallery(episodes("LONG"), ["E1"], title="t", scores=scores)
    opposing = setup_gallery(episodes("SHORT"), ["E1"], title="t", scores=scores)

    assert "✓" in agreeing.layout.annotations[0].text
    assert "✗" in opposing.layout.annotations[0].text


def test_the_title_marks_the_llm_action_and_directional_confidence():
    decision = {
        "action": "veto",
        "verdict": {"long_confidence": 0.21, "short_confidence": 0.72},
    }
    figure = setup_gallery(
        episodes("SHORT"), ["E1"], title="t", llm_actions={"E1": decision}
    )

    title = figure.layout.annotations[0].text
    assert "LLM VETO" in title and "0.72" in title


def test_a_gallery_of_forty_panels_lays_out_without_a_spacing_error():
    """Plotly caps vertical spacing at 1/(rows-1); 40 panels is the ask."""
    many = episodes()
    ids = [f"E{k}" for k in range(40)]
    bars = pd.concat(
        [many.bars.assign(episode_id=episode_id) for episode_id in ids], ignore_index=True
    )
    features = pd.concat([many.features.rename(index={"E1": episode_id}) for episode_id in ids])

    figure = setup_gallery(
        Episodes(features=features, bars=bars, context_bars=CONTEXT),
        ids, title="t", columns=4,
    )
    assert len([t for t in figure.data if t.type == "candlestick"]) == 40


def test_the_peak_marks_the_best_the_trade_ever_looked():
    """Long peaks on the highest high, short on the lowest low, inside the hold."""
    long_peak = traces(setup_gallery(episodes("LONG"), ["E1"], title="t"), "peak")[0]
    short_peak = traces(setup_gallery(episodes("SHORT"), ["E1"], title="t"), "peak")[0]

    # The fixture rises monotonically, so the long peak is the last held bar.
    assert long_peak.x[0] == local(CONTEXT + HOLD)
    assert long_peak.y[0] == pytest.approx(100.0 + HOLD * 0.1 + 0.05)
    # and the short peak is the entry bar, its lowest low.
    assert short_peak.x[0] == local(CONTEXT)
    assert short_peak.y[0] == pytest.approx(100.0 - 0.05)


def test_the_peak_never_looks_past_the_exit():
    figure = setup_gallery(episodes("LONG"), ["E1"], title="t")
    assert traces(figure, "peak")[0].x[0] <= local(CONTEXT + HOLD)


def test_the_axis_is_real_time_not_bars_from_entry():
    """The panel must be readable as a clock, not as an offset counter."""
    figure = setup_gallery(episodes(), ["E1"], title="t")
    candles = next(t for t in figure.data if t.type == "candlestick")

    assert list(candles.x) == list(_market_naive(minute_index(CONTEXT + HOLD + 1)))
    titles = {ax.title.text for ax in figure.select_xaxes()}
    assert "bars from entry" not in titles


def test_a_strategy_without_level_columns_still_renders():
    """The gallery is generic: only sr_momentum publishes watched_level/zone."""
    bare = episodes()
    bare.bars = bare.bars.drop(columns=["watched_level", "zone",
                                        "previous_day_resistance", "previous_day_support"])
    figure = setup_gallery(bare, ["E1"], title="t", forecasts=forecast())

    assert any(t.type == "candlestick" for t in figure.data)
    assert traces(figure, "entry") and traces(figure, "peak") and traces(figure, "Kronos")
    assert not [s for s in figure.layout.shapes if s.type == "rect"], "no zone to draw"


def test_a_scale_out_is_marked_distinctly_from_a_close():
    """A partial exit leaves a smaller position running and would otherwise
    look like an ordinary exit on the panel."""
    scaled = episodes()
    weights = np.where(scaled.bars["offset"] < 0, 0.0, 0.4)
    weights[scaled.bars["offset"].to_numpy() >= 3] = 0.2      # half off at offset 3
    scaled.bars = scaled.bars.assign(target_weight=weights)

    figure = setup_gallery(scaled, ["E1"], title="t")
    marks = traces(figure, "scaled out")
    assert marks and len(marks[0].x) == 1, "the reduction was not marked"


def test_no_marker_when_the_position_is_never_reduced():
    plain = episodes()
    weights = np.where(plain.bars["offset"] < 0, 0.0, 0.4)
    plain.bars = plain.bars.assign(target_weight=weights)
    assert not traces(setup_gallery(plain, ["E1"], title="t"), "scaled out")


def test_a_full_close_is_not_counted_as_a_scale_out():
    plain = episodes()
    weights = np.where(plain.bars["offset"] < 0, 0.0, 0.4)
    weights[plain.bars["offset"].to_numpy() >= 4] = 0.0        # closed, not reduced
    plain.bars = plain.bars.assign(target_weight=weights)
    assert not traces(setup_gallery(plain, ["E1"], title="t"), "scaled out")


# ------------------------------------------------------- consolidation boxes
def _range_state(in_range, *, high=100.6, low=100.1, range_id=0):
    """A market_state-shaped state frame over the fixture's own bars."""
    index = minute_index(CONTEXT + HOLD + 1)
    n = len(index)
    live = np.zeros(n, dtype=bool)
    live[in_range] = True
    return {"AAA": pd.DataFrame(
        {"in_range": live,
         "range_id": np.where(live, range_id, -1),
         "range_high": np.where(live, high, np.nan),
         "range_low": np.where(live, low, np.nan)},
        index=index)}


def _range_boxes(figure):
    return [s for s in figure.layout.shapes
            if s.type == "rect" and s.line is not None
            and s.line.color == RANGE_COLOR]


def test_a_consolidation_box_is_shaded_at_its_own_high_and_low():
    figure = setup_gallery(episodes(), ["E1"], title="t",
                           ranges=_range_state(slice(0, 4)))
    boxes = _range_boxes(figure)
    assert len(boxes) == 1
    assert (boxes[0].y0, boxes[0].y1) == pytest.approx((100.1, 100.6))


def test_two_boxes_in_one_window_are_drawn_separately():
    """One rectangle per range_id: merging them would invent a box that
    spanned a gap the detector had actually closed."""
    state = _range_state(slice(0, 3))
    frame = state["AAA"]
    frame.iloc[6:9, frame.columns.get_loc("in_range")] = True
    frame.iloc[6:9, frame.columns.get_loc("range_id")] = 1
    frame.iloc[6:9, frame.columns.get_loc("range_high")] = 101.4
    frame.iloc[6:9, frame.columns.get_loc("range_low")] = 101.0
    assert len(_range_boxes(setup_gallery(
        episodes(), ["E1"], title="t", ranges=state))) == 2


def test_a_symbol_with_no_range_data_still_renders():
    """The panel degrades, it does not fail — the same contract as levels."""
    figure = setup_gallery(episodes(), ["E1"], title="t", ranges={"ZZZ": pd.DataFrame()})
    assert not _range_boxes(figure)
    assert any(t.type == "candlestick" for t in figure.data)


def test_omitting_ranges_leaves_the_panel_exactly_as_before():
    assert _range_boxes(setup_gallery(episodes(), ["E1"], title="t")) == []


def test_the_title_reports_the_gate_readings_at_the_decision_bar():
    """A panel that shows the outcome without the readings the rule tested
    cannot be used to check the rule."""
    built = episodes()
    built.bars["momentum_z"] = np.where(built.bars["offset"] <= -1, 1.75, np.nan)
    built.bars["relative_volume"] = np.where(built.bars["offset"] <= -1, 2.40, np.nan)
    title = setup_gallery(built, ["E1"], title="t").layout.annotations[0].text
    assert "z +1.75" in title
    assert "rvol 2.40" in title
