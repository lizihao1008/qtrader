"""Loss attribution — the verdict must follow the arithmetic, not the vibe."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qtrader.analysis.attribution import diagnose_shortfall
from qtrader.analysis.episodes import Episodes


def episodes_from(gross_bps, *, cost=3.0, mfe_multiple=1.2, seed=0):
    """Synthetic episodes with a controlled gross-return distribution."""
    gross = np.asarray(gross_bps, dtype=float)
    features = pd.DataFrame(
        {
            "gross_return_bps": gross,
            "cost_bps": cost,
            "net_return_bps": gross - cost,
            "mfe_bps": np.maximum(gross, 0) * mfe_multiple + 1.0,
            "mae_bps": np.minimum(gross, 0) - 1.0,
            "hold_bars": 20,
            "direction": "LONG",
            "gross_win": gross > 0,
        },
        index=[f"e{i}" for i in range(len(gross))],
    )
    return Episodes(features=features, bars=pd.DataFrame())


def test_no_edge_is_called_a_signal_problem():
    rng = np.random.default_rng(1)
    noise = rng.normal(0.0, 40.0, 600)
    verdict = diagnose_shortfall(episodes_from(noise)).verdict
    assert verdict.startswith("signal")


def test_a_reliably_negative_edge_is_called_out_as_adverse():
    rng = np.random.default_rng(2)
    bad = rng.normal(-20.0, 20.0, 600)
    assert diagnose_shortfall(episodes_from(bad)).verdict == "signal (adverse)"


def test_a_real_edge_lost_at_the_exit_is_called_a_holding_problem():
    """Strong, significant gross edge but most of the excursion given back."""
    rng = np.random.default_rng(3)
    gross = rng.normal(20.0, 15.0, 800)
    shortfall = diagnose_shortfall(episodes_from(gross, mfe_multiple=6.0))
    assert shortfall.edge_t_stat > 2.0
    assert shortfall.capture_ratio < 0.35
    assert shortfall.verdict == "holding period"


def test_an_edge_smaller_than_the_friction_is_called_a_cost_problem():
    rng = np.random.default_rng(4)
    gross = rng.normal(2.0, 8.0, 3000)  # small but reliable
    shortfall = diagnose_shortfall(episodes_from(gross, cost=3.0, mfe_multiple=1.1))
    assert shortfall.edge_t_stat > 2.0
    assert shortfall.verdict == "cost / frequency"


def test_a_profitable_run_is_not_blamed_on_anything():
    rng = np.random.default_rng(5)
    gross = rng.normal(12.0, 10.0, 800)
    shortfall = diagnose_shortfall(episodes_from(gross, cost=3.0, mfe_multiple=1.1))
    assert shortfall.verdict.startswith("none")
    assert shortfall.net_per_trade_bps > 0


def test_the_identity_holds():
    """net per trade must equal gross minus cost, however the verdict reads."""
    shortfall = diagnose_shortfall(episodes_from([10.0, -5.0, 3.0, -8.0], cost=2.0))
    assert shortfall.net_per_trade_bps == pytest.approx(
        shortfall.gross_per_trade_bps - shortfall.cost_per_trade_bps
    )
    assert shortfall.trades == 4
