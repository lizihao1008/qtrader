"""Strategy rules and the weight contract."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.strategies import build_strategy
from qtrader.strategies.base import StrategySignals
from qtrader.strategies.cross_sectional import CrossSectionalResidualStrategy
from qtrader.strategies.ma_cross import MACrossStrategy
from tests.conftest import make_context, minute_index


# --------------------------------------------------------------- the contract
def test_gross_weight_may_not_exceed_one():
    index = minute_index(2)
    with pytest.raises(ValueError, match=r"sum\(\|w\|\) <= 1"):
        StrategySignals(target_weights=pd.DataFrame({"A": [0.8, 0.8], "B": [0.8, 0.8]}, index=index))


def test_weights_may_not_contain_nan():
    index = minute_index(2)
    with pytest.raises(ValueError, match="NaN"):
        StrategySignals(target_weights=pd.DataFrame({"A": [0.5, np.nan]}, index=index))


def test_registry_builds_by_name():
    strategy = build_strategy("ma_cross", {"fast": 5, "slow": 20})
    assert isinstance(strategy, MACrossStrategy)
    assert strategy.describe()["fast"] == 5
    with pytest.raises(KeyError):
        build_strategy("does_not_exist")


# ------------------------------------------------------------------ ma_cross
def test_ma_cross_is_flat_during_warmup_then_long_on_an_uptrend():
    prices = [100.0 + i for i in range(12)]
    context = make_context({"AAA": prices, "SPY": [10.0] * 12})
    weights = MACrossStrategy(fast=2, slow=4, flat_time=None).generate(context).target_weights

    assert (weights["AAA"].iloc[:3] == 0).all()  # slow MA undefined
    assert (weights["AAA"].iloc[4:] == 1.0).all()


def test_ma_cross_downtrend_is_flat_when_shorting_is_disabled():
    prices = [100.0 - i for i in range(12)]
    context = make_context({"AAA": prices, "SPY": [10.0] * 12})

    long_only = MACrossStrategy(fast=2, slow=4, allow_short=False, flat_time=None)
    with_shorts = MACrossStrategy(fast=2, slow=4, allow_short=True, flat_time=None)

    assert (long_only.generate(context).target_weights == 0).all(axis=None)
    assert (with_shorts.generate(context).target_weights["AAA"].iloc[4:] == -1.0).all()


def test_ma_cross_splits_capital_across_a_multi_symbol_universe():
    rising = [100.0 + i for i in range(12)]
    context = make_context({"AAA": rising, "BBB": rising, "SPY": [10.0] * 12})
    weights = MACrossStrategy(fast=2, slow=4, flat_time=None).generate(context).target_weights
    assert weights.iloc[-1].tolist() == [0.5, 0.5]


def test_ma_cross_flattens_before_the_close():
    prices = [100.0 + i for i in range(12)]
    context = make_context({"AAA": prices, "SPY": [10.0] * 12})
    shifted = context.index + pd.Timedelta(hours=6, minutes=20)  # 15:50 .. 16:01 local
    context = make_context(
        {"AAA": prices, "SPY": [10.0] * 12},
        tradable=pd.DataFrame(True, index=shifted, columns=["AAA"]),
    )
    context.panel.close.index = shifted  # relabel the same bars onto the late window

    weights = MACrossStrategy(fast=2, slow=4, flat_time="15:55").generate(context).target_weights
    late = weights.index.tz_convert("America/New_York").time >= pd.Timestamp("15:55").time()
    assert (weights.loc[late] == 0).all(axis=None)


def test_ma_cross_will_not_hold_an_untradable_symbol():
    prices = [100.0 + i for i in range(12)]
    context = make_context({"AAA": prices, "SPY": [10.0] * 12})
    context.tradable.loc[context.index[6:], "AAA"] = False

    weights = MACrossStrategy(fast=2, slow=4, flat_time=None).generate(context).target_weights
    assert (weights["AAA"].iloc[6:] == 0).all()


# ------------------------------------------------------- cross_sectional_residual
def _dispersed_context(n_bars: int = 200, n_symbols: int = 10):
    """A cross-section with real, reproducible dispersion around a sector ETF."""
    rng = np.random.default_rng(11)
    common = np.cumsum(rng.normal(0, 0.0008, n_bars))
    prices = {"XLK": list(100.0 * np.exp(common)), "SPY": list(400.0 * np.exp(common))}
    symbols = [f"S{i}" for i in range(n_symbols)]
    for i, symbol in enumerate(symbols):
        idiosyncratic = np.cumsum(rng.normal(0, 0.0015, n_bars))
        prices[symbol] = list((50.0 + i) * np.exp(common + idiosyncratic))
    context = make_context(
        prices,
        symbols=symbols,
        sectors={s: "XLK" for s in symbols},
    )
    return context, symbols


def test_cross_sectional_holds_both_sides_and_respects_the_budget():
    context, _ = _dispersed_context()
    strategy = CrossSectionalResidualStrategy(
        lookback=10, beta_window=30, n_positions=2, min_abs_zscore=0.0,
        rebalance_bars=5, min_eligible=5, flat_time=None,
    )
    weights = strategy.generate(context).target_weights
    active = weights.loc[weights.abs().sum(axis=1) > 0]

    assert not active.empty
    assert active.abs().sum(axis=1).max() <= 1.0 + 1e-9
    assert (active.gt(0).sum(axis=1) <= 2).all()
    assert (active.lt(0).sum(axis=1) <= 2).all()
    # Dollar-neutral by construction when both sides are filled.
    both_sides = active.loc[active.gt(0).any(axis=1) & active.lt(0).any(axis=1)]
    assert both_sides.sum(axis=1).abs().max() == pytest.approx(0.0, abs=1e-9)


def test_a_high_threshold_produces_an_empty_book():
    context, _ = _dispersed_context()
    strategy = CrossSectionalResidualStrategy(
        lookback=10, beta_window=30, min_abs_zscore=99.0, rebalance_bars=5,
        min_eligible=5, flat_time=None,
    )
    assert (strategy.generate(context).target_weights == 0).all(axis=None)


def test_reversion_and_momentum_are_opposite_bets():
    context, _ = _dispersed_context()
    common = dict(
        lookback=10, beta_window=30, n_positions=2, min_abs_zscore=0.0,
        rebalance_bars=5, min_eligible=5, flat_time=None,
    )
    reversion = CrossSectionalResidualStrategy(mode="reversion", **common).generate(context)
    momentum = CrossSectionalResidualStrategy(mode="momentum", **common).generate(context)

    pd.testing.assert_frame_equal(reversion.scores, -momentum.scores)
    pd.testing.assert_frame_equal(reversion.target_weights, -momentum.target_weights)


def test_the_book_is_held_between_rebalances():
    context, _ = _dispersed_context()
    strategy = CrossSectionalResidualStrategy(
        lookback=10, beta_window=30, n_positions=2, min_abs_zscore=0.0,
        rebalance_bars=10, min_eligible=5, flat_time=None,
    )
    weights = strategy.generate(context).target_weights
    changes = weights.ne(weights.shift()).any(axis=1)
    changed_bars = np.flatnonzero(changes.to_numpy())[1:]  # ignore the first row

    assert len(changed_bars) > 0
    assert set(changed_bars % 10) == {0}  # only ever on the rebalance grid


def test_untradable_symbols_are_never_held():
    context, symbols = _dispersed_context()
    context.tradable.loc[:, symbols[0]] = False
    strategy = CrossSectionalResidualStrategy(
        lookback=10, beta_window=30, n_positions=2, min_abs_zscore=0.0,
        rebalance_bars=5, min_eligible=5, flat_time=None,
    )
    assert (strategy.generate(context).target_weights[symbols[0]] == 0).all()


def test_sector_reference_requires_a_sector_map():
    context = make_context({"AAA": [10.0] * 20, "SPY": [5.0] * 20})
    with pytest.raises(ValueError, match="no sector assigned"):
        CrossSectionalResidualStrategy(lookback=2, beta_window=3).generate(context)


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError, match="mode"):
        CrossSectionalResidualStrategy(mode="guess")
    with pytest.raises(ValueError, match="reference"):
        CrossSectionalResidualStrategy(reference="vibes")
    with pytest.raises(ValueError, match="rebalance_bars"):
        CrossSectionalResidualStrategy(rebalance_bars=0)


def test_the_long_leg_can_be_switched_off():
    """A one-sided book pays one leg of cost, so it must be expressible."""
    context, _ = _dispersed_context()
    common = dict(lookback=10, beta_window=30, n_positions=2, min_abs_zscore=0.0,
                  rebalance_bars=5, min_eligible=5, flat_time=None)

    short_only = CrossSectionalResidualStrategy(allow_long=False, **common).generate(context)
    both = CrossSectionalResidualStrategy(**common).generate(context)

    assert (short_only.target_weights <= 0).all(axis=None)
    assert (short_only.target_weights < 0).any(axis=None)
    # With one side enabled it gets the whole budget, not half.
    active = short_only.target_weights.loc[short_only.target_weights.abs().sum(axis=1) > 0]
    assert active.abs().sum(axis=1).max() == pytest.approx(1.0)
    assert (both.target_weights > 0).any(axis=None)


def test_disabling_both_sides_is_rejected():
    with pytest.raises(ValueError, match="allow_long"):
        CrossSectionalResidualStrategy(allow_long=False, allow_short=False)
