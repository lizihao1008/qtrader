"""The setup-feature interface: universal features plus whatever a strategy adds."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.analysis.features import FeatureSet, market_features, setup_features
from qtrader.strategies.base import Strategy, StrategySignals
from qtrader.strategies.cross_sectional import CrossSectionalResidualStrategy
from qtrader.strategies.ma_cross import MACrossStrategy
from tests.conftest import make_context


def trending_context(n: int = 200):
    prices = {
        "AAA": [100.0 + i * 0.05 for i in range(n)],
        "BBB": [50.0 - i * 0.02 for i in range(n)],
        "SPY": [400.0 + i * 0.01 for i in range(n)],
        "XLK": [80.0 + i * 0.01 for i in range(n)],
    }
    return make_context(
        prices, symbols=["AAA", "BBB"], sectors={"AAA": "XLK", "BBB": "XLK"}
    )


class BareStrategy(Strategy):
    """A strategy that declares nothing — the zero-configuration case."""

    name = "bare"

    def generate(self, context):
        return StrategySignals(
            target_weights=pd.DataFrame(
                0.0, index=context.index, columns=list(context.symbols)
            )
        )


# ------------------------------------------------------------------ universal
def test_universal_features_need_no_strategy_cooperation():
    context = trending_context()
    strategy = BareStrategy()
    features = setup_features(strategy, strategy.generate(context), context)

    assert {"stock_vol_bps", "relative_volume", "vwap_distance_bps"} <= set(features.names)
    assert {"minute_of_session", "n_eligible", "market_vol_bps"} <= set(features.names)


def test_universal_features_are_causal():
    """Tampering with later bars must not change an earlier feature value."""
    n, cut = 200, 120
    calm = trending_context(n)
    spiked = trending_context(n)
    spiked.panel.close.iloc[cut:, :] *= 3.0

    before = market_features(calm).per_symbol["stock_vol_bps"]["AAA"].iloc[:cut]
    after = market_features(spiked).per_symbol["stock_vol_bps"]["AAA"].iloc[:cut]
    pd.testing.assert_series_equal(before, after)


def test_sampling_reads_one_symbol_at_one_bar():
    context = trending_context()
    strategy = BareStrategy()
    features = setup_features(strategy, strategy.generate(context), context)

    row = features.sample("AAA", 150)
    assert set(row) == set(features.names)
    assert row["minute_of_session"] == 150


def test_a_symbol_missing_from_a_frame_yields_nan_rather_than_an_error():
    partial = FeatureSet(per_symbol={"thing": pd.DataFrame({"AAA": [1.0, 2.0]})})
    assert pd.isna(partial.sample("ZZZ", 0)["thing"])


# ------------------------------------------------------------------- strategy
def test_a_strategy_contributes_its_own_scale_free_view():
    context = trending_context()
    strategy = MACrossStrategy(fast=5, slow=20, flat_time=None)
    features = setup_features(strategy, strategy.generate(context), context)

    assert "ma_gap_bps" in features.names
    # The gap is a fraction of price, so both symbols live on the same scale
    # despite trading at 100 and 50.
    gap = features.per_symbol["ma_gap_bps"].dropna()
    assert gap.abs().max().max() < 5_000


def test_the_cross_sectional_strategy_exposes_its_ranking_score():
    context = trending_context()
    strategy = CrossSectionalResidualStrategy(
        lookback=10, beta_window=30, min_eligible=2, flat_time=None
    )
    features = setup_features(strategy, strategy.generate(context), context)

    assert {"score", "abs_score", "move_in_vols", "residual_signal_bps"} <= set(features.names)
    assert (features.per_symbol["abs_score"].dropna() >= 0).all().all()


def test_strategy_features_win_a_name_clash_and_the_universal_one_is_kept():
    universal = FeatureSet(per_symbol={"stock_vol_bps": pd.DataFrame({"AAA": [1.0]})})
    strategy_view = FeatureSet(per_symbol={"stock_vol_bps": pd.DataFrame({"AAA": [9.0]})})

    merged = universal.merge(strategy_view, prefix="market_")
    assert merged.per_symbol["stock_vol_bps"].iat[0, 0] == 9.0
    assert merged.per_symbol["market_stock_vol_bps"].iat[0, 0] == 1.0


def test_strategy_frames_are_aligned_onto_the_context():
    class Partial(BareStrategy):
        def setup_features(self, signals, context):
            # Deliberately short and missing a symbol.
            return {"thing": pd.DataFrame({"AAA": [1.0, 2.0]}, index=context.index[:2])}

    context = trending_context()
    strategy = Partial()
    features = setup_features(strategy, strategy.generate(context), context)

    frame = features.per_symbol["thing"]
    assert list(frame.columns) == list(context.symbols)
    assert len(frame) == len(context.index)
    assert pytest.approx(frame["AAA"].iloc[0]) == 1.0
    assert frame["BBB"].isna().all()
