"""The one-day workbench: warm-up, isolation of the target session, and reuse.

The notebook is a thin caller (CLAUDE.md §18), so the behaviour that could
mislead someone tuning against a single day is tested here rather than there.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qtrader.data.sessions import session_date
from qtrader.experiments.session_lab import SessionLab

CONFIG = "config/backtest/sr_momentum_index_5min.yaml"
DAY = "2026-01-29"


@pytest.fixture(scope="module")
def lab() -> SessionLab:
    return SessionLab(CONFIG, "QQQ", DAY)


def test_the_session_index_is_only_the_day_under_study(lab):
    days = set(session_date(lab.session_index).to_numpy())
    assert days == {pd.Timestamp(DAY).date()}
    assert 70 <= len(lab.session_index) <= 80, "a regular 5-minute session"


def test_history_before_the_day_is_loaded_for_warm_up(lab):
    """Momentum, ATR and relative volume all need bars before the session."""
    earlier = lab.context.index[lab.context.index < lab.session_index[0]]
    assert len(earlier) > 5 * 78, "not enough warm-up for the 5-session rvol median"


def test_only_the_target_days_trades_are_reported(lab):
    trades = lab.trades(lab.run())
    if trades.empty:
        pytest.skip("no trades that day")
    assert set(session_date(pd.DatetimeIndex(trades["entry_time"])).to_numpy()) \
        == {pd.Timestamp(DAY).date()}
    assert (trades["symbol"] == "QQQ").all()


def test_overrides_change_the_result_and_the_context_is_reused(lab):
    before = id(lab.context)
    loose = lab.summary(lab.run(momentum_z_min=0.1))
    strict = lab.summary(lab.run(momentum_z_min=5.0))

    assert id(lab.context) == before, "the bars were reloaded"
    assert strict["trades"] <= loose["trades"], "a stricter gate cannot trade more"


def test_an_impossible_gate_yields_an_empty_but_valid_summary(lab):
    summary = lab.summary(lab.run(momentum_z_min=99.0))
    assert summary["trades"] == 0 and summary["net_pnl"] == 0.0


def test_a_sweep_returns_one_row_per_value(lab):
    table = lab.sweep("momentum_z_min", [0.25, 1.0])
    assert list(table.index) == [0.25, 1.0]
    assert {"trades", "net_pnl", "gross_bps", "hit_rate"} <= set(table.columns)


def test_a_symbol_outside_the_universe_is_loaded_if_it_has_data():
    """The lab adds the name for this session rather than refusing it."""
    lab = SessionLab(CONFIG, "NVDA", DAY)
    assert "NVDA" in lab.context.symbols
    assert len(lab.session_index) > 0


def test_an_unknown_name_is_refused(monkeypatch):
    from qtrader.data.alpaca_client import UnknownSymbolError

    def refuse(symbols, *args, **kwargs):
        raise UnknownSymbolError("'ZZZZZZ' is not listed on Alpaca")

    monkeypatch.setattr("qtrader.experiments.session_lab.ensure_bars", refuse)
    with pytest.raises(UnknownSymbolError, match="not listed"):
        SessionLab(CONFIG, "ZZZZZZ", DAY)


def test_nikkei_resolves_to_the_alpaca_listing():
    from qtrader.data.alpaca_client import resolve_symbol

    assert resolve_symbol("Nikkei") == "EWJ"
    assert resolve_symbol("n225") == "EWJ"


def test_a_day_with_no_bars_is_refused():
    with pytest.raises(ValueError, match="no bars"):
        SessionLab(CONFIG, "QQQ", "2026-01-25")      # a Sunday


# ------------------------------------------------------------- trigger detail
def test_triggers_are_read_at_the_decision_bar_not_the_fill(lab):
    """Reading at the fill would show a state the rule never saw."""
    run = lab.run()
    triggers = lab.triggers(run)
    if triggers.empty:
        pytest.skip("no trades that day")

    index = lab.context.index
    lag = lab._config.execution.execution_lag_bars
    indicators = run.result.signals.indicators["QQQ"]

    for _, row in triggers.iterrows():
        fill = pd.Timestamp(f"{DAY} {row['fill']}", tz="America/New_York").tz_convert("UTC")
        at = list(index).index(fill) - lag
        assert index[at].tz_convert("America/New_York").strftime("%H:%M") == row["decision"]
        assert row["momentum_z"] == pytest.approx(indicators["momentum_z"].iloc[at])


def test_every_gate_passes_on_a_trade_that_happened(lab):
    """A trade exists only when the whole conjunction held."""
    triggers = lab.triggers(lab.run())
    if triggers.empty:
        pytest.skip("no trades that day")
    for column in ("side ok", "accept ok", "mom ok", "rvol ok", "vwap ok"):
        assert triggers[column].all(), f"{column} was False on a trade that fired"


def test_triggers_is_empty_when_nothing_traded(lab):
    assert lab.triggers(lab.run(momentum_z_min=99.0)).empty


def test_trigger_thresholds_come_from_the_actual_overridden_run(lab):
    triggers = lab.triggers(
        lab.run(momentum_z_min=0.1, rvol_min=0.25, acceptance_bars=0)
    )
    if triggers.empty:
        pytest.skip("no trades under the loose override")

    assert (triggers["mom min"] == 0.1).all()
    assert (triggers["rvol min"] == 0.25).all()
    assert (triggers["accept min"] == 0).all()


def test_trigger_detail_reconstructs_the_break_and_risk_geometry(lab):
    triggers = lab.triggers(lab.run())
    if triggers.empty:
        pytest.skip("no trades that day")

    assert triggers["break ok"].all()
    assert (triggers["break margin bps"] > 0).all()
    assert (triggers["hold margin bps"] > 0).all()
    assert triggers["initial stop bps"].to_numpy() == pytest.approx(
        triggers[["sigma stop bps", "ATR stop bps"]].min(axis=1).to_numpy()
    )


def test_formula_audit_expands_every_trade_without_future_outcomes(lab):
    run = lab.run()
    triggers = lab.triggers(run)
    audit = lab.formula_audit(run)
    if triggers.empty:
        pytest.skip("no trades that day")

    assert len(audit) == 8 * len(triggers)
    assert set(audit.index.get_level_values("rule")) == {
        "1 break", "2 hold side", "3 acceptance", "4 momentum",
        "5 participation", "6 VWAP context", "7 initial risk", "8 execution",
    }
    assert audit["formula"].str.len().gt(10).all()
    assert audit["actual substitution"].str.len().gt(10).all()


def test_trigger_windows_mark_decision_and_fill_without_conflating_them(lab):
    run = lab.run()
    triggers = lab.triggers(run)
    windows = lab.trigger_windows(run)
    if triggers.empty:
        pytest.skip("no trades that day")

    assert set(windows) == set(triggers.index)
    for frame in windows.values():
        assert (frame["role"].str.contains("decision")).sum() == 1
        assert (frame["role"] == "fill (not used by signal)").sum() == 1
        assert frame.index.tz is not None
        assert {"close", "watched_level", "momentum_z", "initial_stop_bps"} \
            <= set(frame.columns)
