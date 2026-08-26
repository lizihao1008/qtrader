"""Features must be causal: the value at t may not depend on data after t."""

from __future__ import annotations

import pandas as pd

from qtrader.features.stock import intraday_vwap, log_return, macd, sma
from tests.conftest import make_bars


def test_sma_matches_manual_average_and_warms_up():
    close = make_bars([1.0, 2.0, 3.0, 4.0])["close"]
    values = sma(close, 3)
    assert values.iloc[:2].isna().all()  # not enough history yet
    assert values.iloc[2] == 2.0
    assert values.iloc[3] == 3.0


def test_indicators_do_not_change_when_future_bars_change():
    """The canonical no-lookahead check for the whole feature module."""
    prices = [10.0, 10.5, 11.0, 10.8, 11.4, 12.0, 11.7, 12.3]
    original = make_bars(prices)["close"]
    tampered = make_bars(prices[:4] + [99.0, 98.0, 97.0, 96.0])["close"]

    cut = 4
    for compute in (lambda s: sma(s, 3), lambda s: log_return(s, 2), lambda s: macd(s)["macd"]):
        a = compute(original).iloc[:cut]
        b = compute(tampered).iloc[:cut]
        pd.testing.assert_series_equal(a, b)


def test_intraday_vwap_resets_each_session():
    day_one = make_bars([10.0, 20.0])
    day_two = make_bars([100.0, 200.0])
    day_two.index = day_two.index + pd.Timedelta(days=1)
    bars = pd.concat([day_one, day_two])

    vwap = intraday_vwap(bars)
    assert vwap.iloc[0] == 10.0  # first bar of day 1
    assert vwap.iloc[1] == 15.0  # cumulative within day 1
    assert vwap.iloc[2] == 100.0  # reset on day 2, not 43.3
