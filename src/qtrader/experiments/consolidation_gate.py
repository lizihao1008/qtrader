"""Use the `market_state` consolidation detector as an entry gate.

The rule under test, stated by the instruction that prompted it:

    while price is inside a detected range, do nothing; only once it has left
    the range may momentum confirm an entry.

The detector itself lives in the sibling repository
``/Users/zihao/work/market_state`` (``market_state.structure.consolidation``)
and is **not** copied here. It is a state machine over bars ``<= t``: a run of
``min_bars`` narrow, directionless bars seeds a range with frozen edges, and two
consecutive closes beyond an edge plus a buffer confirm the break. That repo's
leakage suite pins its causality (truncation and perturbation invariance), which
is the reason it is worth importing rather than reimplementing.

Two things about the coupling, both deliberate:

* the dependency is one-way and lives in this module only. `SRMomentumStrategy`
  takes an opaque ``entry_veto`` frame and never learns what a range is, so the
  gate can be swapped for any other regime detector without touching it;
* `market_state` reads the same clean parquet store this repo writes, at the
  same feed, so both sides see identical bars. Nothing is re-ingested.

Grid
----
The detector's thresholds are stated in bars and were chosen on 5-minute bars
(``min_bars: 9`` = 45 minutes). It is therefore always run on the 5-minute
series. When the decision grid is finer, the mask is carried down with
:func:`qtrader.features.multiframe.align_to_fine`, which makes a 5-minute value
visible only once its bar has closed — without that shift the gate would know a
range had ended up to four minutes before it could have.
"""

from __future__ import annotations

import pandas as pd

from ..features.multiframe import align_to_fine

#: Where the detector's own defaults live. Overriding thresholds from this repo
#: would be tuning someone else's detector against this repo's P&L, so the
#: config path is a parameter and its contents are used as they are.
DEFAULT_CONFIG = "/Users/zihao/work/market_state/configs/default.yaml"


def _detector(config_path: str):
    """Import lazily: only this experiment needs the sibling package."""
    try:
        from market_state.config import load_config
        from market_state.features.primitives import wilder_atr
        from market_state.structure.consolidation import detect_consolidation
    except ModuleNotFoundError as error:  # pragma: no cover - environment issue
        raise ModuleNotFoundError(
            "the consolidation gate needs the market_state package: "
            "pip install -e /Users/zihao/work/market_state --no-deps"
        ) from error
    return load_config(config_path), wilder_atr, detect_consolidation


def range_state(
    panel, symbols, *, config_path: str = DEFAULT_CONFIG
) -> dict[str, pd.DataFrame]:
    """Per-symbol detector state on the panel's own grid.

    ``panel`` must carry the timeframe the detector was configured for; this
    function does not resample, because resampling OHLC would change what the
    detector sees and quietly invalidate its thresholds.
    """
    cfg, wilder_atr, detect_consolidation = _detector(config_path)
    out = {}
    for symbol in symbols:
        bars = pd.DataFrame({
            field: panel.field(field)[symbol]
            for field in ("open", "high", "low", "close")
        }).dropna()
        if bars.empty:
            continue
        atr = wilder_atr(
            bars["high"].to_numpy(float), bars["low"].to_numpy(float),
            bars["close"].to_numpy(float), int(cfg.features.atr_period),
        )
        state, _ = detect_consolidation(bars, atr, cfg)
        out[symbol] = state
    return out


def in_range_mask(
    panel, symbols, *, fine_index=None, config_path: str = DEFAULT_CONFIG
) -> pd.DataFrame:
    """``timestamp x symbol`` veto: True while the symbol is inside a range.

    ``in_range`` is the detector's own inclusive flag — it stays True through
    the pending bars of an unconfirmed break, which is the conservative reading
    and the one the instruction asks for: a break that has not been confirmed is
    not yet outside the range.

    Pass ``fine_index`` to carry a 5-minute mask onto a finer decision grid.
    """
    state = range_state(panel, symbols, config_path=config_path)
    mask = pd.DataFrame(
        {symbol: frame["in_range"].astype(bool) for symbol, frame in state.items()}
    )
    mask = mask.reindex(index=panel.index).fillna(False)
    if fine_index is None:
        return mask
    carried = pd.DataFrame(
        {symbol: align_to_fine(mask[symbol], fine_index) for symbol in mask.columns}
    )
    return carried.fillna(False).astype(bool)


# --------------------------------------------------------- persistence model
#: The sibling repo's walk-forward artifact. Its `predictions_*.parquet` holds
#: **out-of-sample** probabilities from an expanding 12-fold split with purge
#: and embargo — the only version fit to use here. The `final_*.joblib` fit
#: saw every row and would be in-sample across most of any backtest window.
DEFAULT_PREDICTIONS = (
    "/Users/zihao/work/market_state/artifacts/"
    "consolidation-ml-20260907T140349Z-89f7fe965027/predictions_gbdt.parquet"
)


def persistence_probability(index, symbols, *, path: str = DEFAULT_PREDICTIONS) -> pd.DataFrame:
    """``timestamp x symbol`` P(every one of the next 6 closes stays in the box).

    Rows exist only while a box is being tracked; elsewhere the value is NaN,
    which is not "the box will break" but "there is no box". Callers must treat
    the two differently.

    Timing: a row stamped ``t`` carries ``decision_time = t + one bar``, i.e. it
    is built from bars up to and including ``t`` and is knowable when ``t``
    closes. That is exactly when this repo makes its decision on bar ``t``, so
    the frame is used on its own index with no further shift. The check is
    asserted rather than assumed, because a silent off-by-one here would be
    indistinguishable from an edge.
    """
    frame = pd.read_parquet(
        path, columns=["symbol", "timestamp", "decision_time", "pred_persist"]
    )
    step = frame["decision_time"] - frame["timestamp"]
    if step.nunique() != 1 or step.iloc[0] <= pd.Timedelta(0):
        raise ValueError("predictions do not carry one uniform decision lag")
    wide = frame.pivot_table(
        index="timestamp", columns="symbol", values="pred_persist", aggfunc="last"
    )
    return wide.reindex(index=pd.DatetimeIndex(index), columns=list(symbols))


def persistence_veto(index, symbols, *, threshold: float, path: str = DEFAULT_PREDICTIONS):
    """Veto a new entry while the model says the box is likely to hold.

    This is the binary gate with the model deciding *which* in-range bars are
    worth standing aside for. ``threshold = 0`` reproduces the binary gate over
    the bars the model covers; ``threshold = 1`` vetoes nothing.
    """
    probability = persistence_probability(index, symbols, path=path)
    # NaN means no box, which is not a veto.
    return (probability >= threshold).fillna(False)
