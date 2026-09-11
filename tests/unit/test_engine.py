"""Engine timing, sizing and the no-lookahead invariant."""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.backtest.costs import CostModel
from qtrader.backtest.engine import BacktestEngine, ExecutionConfig
from qtrader.data.panel import BarPanel
from qtrader.strategies.base import MarketContext, StrategySignals
from qtrader.strategies.ma_cross import MACrossStrategy
from qtrader.universe.definition import Universe
from tests.conftest import make_bars, make_context

FREE = CostModel(half_spread_bps=0.0, slippage_bps=0.0)


def weights_from(context: MarketContext, values: dict[str, list[float]]) -> StrategySignals:
    return StrategySignals(
        target_weights=pd.DataFrame(values, index=context.index, dtype=float)
    )


def test_execution_lag_below_one_bar_is_rejected():
    with pytest.raises(ValueError, match="execution_lag_bars"):
        ExecutionConfig(execution_lag_bars=0)


def test_gross_weight_above_one_is_rejected():
    context = make_context({"AAA": [10.0, 10.0], "BBB": [10.0, 10.0], "SPY": [5.0, 5.0]})
    with pytest.raises(ValueError, match=r"sum\(\|w\|\) <= 1"):
        weights_from(context, {"AAA": [0.7, 0.7], "BBB": [0.7, 0.7]})


def test_signal_is_filled_on_the_next_bar_open():
    context = make_context(
        {"AAA": [100.0, 100.0, 110.0, 110.0], "SPY": [10.0] * 4},
        opens={"AAA": [100.0, 100.0, 105.0, 110.0]},
    )
    # Decide to go long on bar 1; the fill must happen at bar 2's open (105), not
    # at bar 1's close (100) and not at bar 2's close (110).
    signals = weights_from(context, {"AAA": [0.0, 1.0, 1.0, 0.0]})
    result = BacktestEngine(
        FREE, ExecutionConfig(initial_cash=10_000.0, gross_leverage=1.0)
    ).run(context, signals)

    first_fill = result.fills.iloc[0]
    assert first_fill["timestamp"] == context.index[2]
    assert first_fill["reference_price"] == pytest.approx(105.0)
    assert first_fill["shares"] == 95  # floor(10_000 / 105)


def test_a_pending_target_is_cancelled_at_the_session_boundary():
    """Execution lag is measured in bars, but day orders do not rest overnight."""
    index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-08-03 15:55", tz="America/New_York"),
            pd.Timestamp("2026-08-04 09:30", tz="America/New_York"),
            pd.Timestamp("2026-08-04 09:31", tz="America/New_York"),
        ]
    ).tz_convert("UTC")
    panel = BarPanel.from_frames(
        {
            "AAA": make_bars([100.0] * 3, index=index),
            "SPY": make_bars([10.0] * 3, index=index),
        }
    )
    context = MarketContext(
        panel=panel,
        universe=Universe(name="test", symbols=("AAA",), benchmark="SPY"),
        tradable=pd.DataFrame(True, index=index, columns=["AAA"]),
    )
    signals = weights_from(context, {"AAA": [1.0, 0.0, 0.0]})

    result = BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0)).run(
        context, signals
    )

    assert result.fills.empty, "the prior session's target filled at the next open"


def test_weights_split_capital_across_symbols():
    context = make_context({"AAA": [50.0] * 3, "BBB": [20.0] * 3, "SPY": [10.0] * 3})
    signals = weights_from(context, {"AAA": [0.5] * 3, "BBB": [-0.5] * 3})
    result = BacktestEngine(
        FREE, ExecutionConfig(initial_cash=1_000.0, gross_leverage=1.0)
    ).run(context, signals)

    fills = result.fills.set_index("symbol")
    assert fills.loc["AAA", "shares"] == 10  # floor(1_000 * 0.5 / 50)
    assert fills.loc["BBB", "shares"] == -25  # floor(1_000 * 0.5 / 20), sold short


def test_equity_and_returns_are_marked_at_the_close():
    context = make_context(
        {"AAA": [100.0, 100.0, 120.0], "SPY": [10.0] * 3},
        opens={"AAA": [100.0, 100.0, 100.0]},
    )
    signals = weights_from(context, {"AAA": [1.0, 1.0, 1.0]})
    result = BacktestEngine(
        FREE, ExecutionConfig(initial_cash=1_000.0, gross_leverage=1.0)
    ).run(context, signals)

    curve = result.equity_curve
    # 10 shares bought at 100 on bar 2, marked at 120 -> +200 on 1000 of equity.
    assert curve["equity"].iloc[-1] == pytest.approx(1_200.0)
    assert curve["cum_return"].iloc[-1] == pytest.approx(0.2)
    assert curve["gross_exposure"].iloc[-1] == pytest.approx(1_200.0)


def test_costs_reduce_the_result_relative_to_a_frictionless_run():
    context = make_context(
        {"AAA": [100.0, 101.0, 102.0, 103.0], "SPY": [10.0] * 4},
        opens={"AAA": [100.0, 100.5, 101.5, 102.5]},
    )
    signals = weights_from(context, {"AAA": [1.0, 1.0, 0.0, 0.0]})
    config = ExecutionConfig(initial_cash=10_000.0, gross_leverage=1.0)

    free = BacktestEngine(FREE, config).run(context, signals)
    costly = BacktestEngine(
        CostModel(half_spread_bps=5.0, slippage_bps=5.0, commission_per_share=0.01), config
    ).run(context, signals)

    assert costly.metrics["total_return"] < free.metrics["total_return"]
    assert costly.metrics["total_costs"] > 0


def test_unchanged_weights_do_not_generate_noise_trades():
    """A held position must not be re-sized bar by bar."""
    context = make_context({"AAA": [100.0, 101.0, 103.0, 99.0, 104.0], "SPY": [10.0] * 5})
    signals = weights_from(context, {"AAA": [1.0] * 5})
    result = BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0)).run(context, signals)

    assert len(result.fills) == 1  # one entry, then hold
    assert result.equity_curve["n_positions"].iloc[1:].eq(1).all()


def test_exposure_cannot_be_opened_in_a_symbol_that_did_not_print():
    context = make_context({"AAA": [100.0] * 5, "SPY": [10.0] * 5})
    context.tradable.loc[:, "AAA"] = False
    signals = weights_from(context, {"AAA": [1.0] * 5})

    result = BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0)).run(context, signals)
    assert result.fills.empty


def test_strategy_may_not_request_untradable_symbols():
    context = make_context({"AAA": [10.0, 10.0], "SPY": [5.0, 5.0]})
    signals = StrategySignals(
        target_weights=pd.DataFrame({"SPY": [1.0, 1.0]}, index=context.index)
    )
    with pytest.raises(ValueError, match="untradable symbols"):
        BacktestEngine(FREE).run(context, signals)


def test_future_bars_cannot_change_past_fills():
    """End-to-end leakage guard over strategy + engine."""
    prices = [100.0 + (i % 7) * 0.4 for i in range(80)]
    tampered = prices[:50] + [500.0] * 30

    engine = BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0))
    strategy = MACrossStrategy(fast=3, slow=10, flat_time=None)

    def fills_before_cut(price_path):
        """Fills that executed strictly before the first tampered bar."""
        context = make_context({"AAA": price_path, "SPY": [10.0] * len(price_path)})
        result = engine.run(context, strategy.generate(context))
        cut = context.index[50]
        return result.fills.loc[result.fills["timestamp"] < cut].reset_index(drop=True)

    pd.testing.assert_frame_equal(fills_before_cut(prices), fills_before_cut(tampered))


def test_no_fill_is_possible_on_a_bar_that_did_not_print():
    """A resting order in a bar where nothing traded does not get filled.

    The liquidity mask tolerates a few silent bars — that is a judgement about
    whether a symbol is worth trading. Whether a fill can happen at all is a
    different question, answered by the execution bar itself.
    """
    context = make_context({"AAA": [100.0] * 6, "SPY": [10.0] * 6})
    # Liquid by every measure, but the entry bar itself has no print.
    context.panel.traded.loc[context.index[2], "AAA"] = False
    signals = weights_from(context, {"AAA": [0.0, 1.0, 1.0, 1.0, 1.0, 1.0]})

    result = BacktestEngine(FREE, ExecutionConfig(initial_cash=10_000.0)).run(context, signals)
    assert result.fills.iloc[0]["timestamp"] == context.index[3]  # deferred, not filled at 2
