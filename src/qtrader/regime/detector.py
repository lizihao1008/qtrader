"""Online FLAT / UP / DOWN detector: Kalman slope → CUSUM → ER + hysteresis.

One :meth:`update` per closed bar. :meth:`run` is that same step over a frame —
it never uses a future row to compute a past state.

Internal measurement is the session-anchored log close
``y_t = log(close_t) - log(close_session_open)`` so the Kalman state is not
tied to the dollar price level. The overnight gap is not a 1-minute return:
a new session re-initialises the filter, CUSUM, ER and the regime (to FLAT).
The last causal ``R`` is kept as a scale prior so the open is not blind.

Everything the state machine tests is measured against a **driftless random
walk**:

* ``slope_z = slope / slope_rw_std`` — standard normal under that null
  (:mod:`qtrader.regime.kalman`), not the model's own posterior sd, which is
  misspecified and whose spread varies twofold across symbols;
* ``er_rw = ER_n * sqrt(n)`` — mean 1.0 under that null for *any* ``n``, so the
  entry gate and the tightened hold gate can share one scale.

Once the state leaves FLAT, Kaufman ER is computed only on bars of *this*
regime. If ``slope_z`` fades (below ``weaken_z``, or below ``weaken_frac`` of
the post-entry peak) the ER window shrinks to ``er_tighten_window``; a window
that is no more directional than noise, or one that has cleanly retraced,
returns the state to FLAT. The weaken path never jumps straight to the opposite
regime — that has to earn its own entry, or trip ``reversal_h`` / ``reversal_z``
— and the weaken flag is released again if ``slope_z`` recovers.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from math import log, sqrt
from typing import Any

import numpy as np
import pandas as pd

from ..data.sessions import session_date
from .config import KalmanCUSUMConfig
from .cusum import update_cusum
from .kalman import LocalLinearTrendFilter

FLAT, UP, DOWN = "FLAT", "UP", "DOWN"
SNAPSHOT_FIELDS = (
    "level",
    "slope",
    "slope_std",
    "slope_rw_std",
    "slope_z",
    "realized_vol",
    "cusum_up",
    "cusum_down",
    "er_rw",
    "signed_er_rw",
    "er_bars",
    "weakened",
    "state",
    "state_changed",
    "signal",
    "measurement_var",
    "smooth_close",
)


class KalmanCUSUMRegimeDetector:
    """Causal 1-minute regime detector.

    Parameters
    ----------
    config
        :class:`KalmanCUSUMConfig`, a dict of overrides, or ``None`` (balanced).
    """

    def __init__(self, config: KalmanCUSUMConfig | Mapping[str, Any] | None = None) -> None:
        if config is None:
            self.cfg = KalmanCUSUMConfig()
        elif isinstance(config, KalmanCUSUMConfig):
            self.cfg = config
        else:
            self.cfg = KalmanCUSUMConfig.from_dict(dict(config))
        self._kf = LocalLinearTrendFilter(
            lambda_level=self.cfg.lambda_level,
            lambda_slope=self.cfg.lambda_slope,
            process_noise=self.cfg.process_noise,
            epsilon=self.cfg.epsilon,
        )
        self._R = self.cfg.r_floor
        self._reset_stream()

    @classmethod
    def from_preset(cls, name: str) -> "KalmanCUSUMRegimeDetector":
        return cls(KalmanCUSUMConfig.from_preset(name))

    def reset(self) -> None:
        """Forget everything, including the carried measurement-variance prior."""
        self._R = self.cfg.r_floor
        self._reset_stream()

    def update(self, bar: pd.Series | Mapping[str, Any]) -> dict[str, Any]:
        """Consume one closed bar; return the snapshot at that bar."""
        close, ts = _parse_bar(bar)
        return self._step(close, ts)

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Replay ``df`` by calling the same online step once per row, in order."""
        if df.empty:
            out = pd.DataFrame(columns=list(SNAPSHOT_FIELDS))
            out.index.name = getattr(df.index, "name", None) or "timestamp"
            return out
        if "close" not in df.columns:
            raise KeyError("bar frame must contain a 'close' column")
        sessions = _session_keys(df.index)
        records = [
            self._step(float(close), ts, session=key)
            for ts, close, key in zip(df.index, df["close"].to_numpy(), sessions)
        ]
        out = pd.DataFrame.from_records(records, index=df.index)
        out.index.name = df.index.name or "timestamp"
        return out

    def plot(self, bars: pd.DataFrame, result: pd.DataFrame | None = None, **kwargs):
        """Delegates to :func:`qtrader.viz.regime.plot_regime`."""
        from ..viz.regime import plot_regime

        if result is None:
            result = self.run(bars)
        kwargs.setdefault("config", self.cfg)
        return plot_regime(bars, result, **kwargs)

    # ------------------------------------------------------------------ internals
    def _reset_stream(self) -> None:
        self._session = None
        self._started = False
        self._log_origin = 0.0
        self._prev_log: float | None = None
        self._r_buf: deque[float] = deque(maxlen=self.cfg.r_window)
        self._vol_buf: deque[float] = deque(maxlen=self.cfg.vol_window)
        self._log_er: deque[float] = deque(maxlen=self.cfg.er_window + 1)
        self._regime_log: list[float] = []
        self._cusum_up = 0.0
        self._cusum_down = 0.0
        self._state = FLAT
        self._adverse = 0
        self._n_returns = 0
        self._peak_abs_z = 0.0
        self._weakened = False

    def _begin_session(self, close: float, session: object) -> None:
        self._session = session
        self._log_origin = log(close)
        self._prev_log = None
        self._vol_buf.clear()
        self._log_er.clear()
        self._regime_log.clear()
        self._cusum_up = 0.0
        self._cusum_down = 0.0
        self._state = FLAT
        self._adverse = 0
        self._n_returns = 0
        self._peak_abs_z = 0.0
        self._weakened = False
        self._kf.reset(0.0, self._R)
        self._started = True

    def _needs_new_session(self, session: object) -> bool:
        if not self._started:
            return True
        if not self.cfg.reset_on_session or session is None:
            return False
        return session != self._session

    def _step(
        self, close: float, ts: pd.Timestamp | None, session: object | None = None
    ) -> dict[str, Any]:
        if close <= 0.0 or not np.isfinite(close):
            raise ValueError(f"close must be a positive finite price, got {close}")
        if session is None and ts is not None:
            session = _session_key(ts)
        if self._needs_new_session(session):
            self._begin_session(close, session)
        elif session is not None:
            self._session = session

        log_p = log(close)
        y = log_p - self._log_origin
        if self._prev_log is not None:
            ret = log_p - self._prev_log
            self._r_buf.append(ret)
            self._vol_buf.append(ret)
            self._n_returns += 1
            self._refresh_R()
        self._prev_log = log_p
        self._log_er.append(log_p)
        self._regime_log.append(log_p)

        step = self._kf.step(y, self._R)
        slope_z = step.slope / max(step.slope_rw_std, self.cfg.epsilon)
        slope_z = float(np.clip(slope_z, -self.cfg.z_clip, self.cfg.z_clip))
        realized_vol = _std(self._vol_buf, self.cfg.vol_window)

        prev_state = self._state
        if self.cfg.anchor_er_to_regime and prev_state in (UP, DOWN):
            self._update_weakened(slope_z)
        er_rw, signed_er_rw, er_bars = self._current_er(prev_state)
        signal = self._signal(step.slope, slope_z, realized_vol)
        ready = self._ready(realized_vol)

        if ready and np.isfinite(signal):
            self._cusum_up, self._cusum_down = update_cusum(
                self._cusum_up, self._cusum_down, signal, self.cfg.cusum_k
            )
            self._apply_state_machine(slope_z, er_rw, signed_er_rw)

        state_changed = self._state != prev_state
        cusum_up, cusum_down = self._cusum_up, self._cusum_down
        weakened = self._weakened
        if state_changed:
            # Snapshot keeps the crossing value; the next bar starts clean.
            self._cusum_up = 0.0
            self._cusum_down = 0.0
            self._adverse = 0
            if self.cfg.anchor_er_to_regime:
                self._regime_log = [log_p]
                self._weakened = False
                self._peak_abs_z = abs(slope_z) if self._state in (UP, DOWN) else 0.0

        return {
            "level": step.level,
            "slope": step.slope,
            "slope_std": step.slope_std,
            "slope_rw_std": step.slope_rw_std,
            "slope_z": slope_z,
            "realized_vol": realized_vol,
            "cusum_up": cusum_up,
            "cusum_down": cusum_down,
            "er_rw": er_rw,
            "signed_er_rw": signed_er_rw,
            "er_bars": er_bars,
            "weakened": weakened,
            "state": self._state,
            "state_changed": state_changed,
            "signal": signal,
            "measurement_var": self._R,
            "smooth_close": float(np.exp(step.level + self._log_origin)),
        }

    def _signal(self, slope: float, slope_z: float, realized_vol: float) -> float:
        if self.cfg.signal_mode == "kalman_z":
            return slope_z
        if not np.isfinite(realized_vol):
            return float("nan")
        raw = slope / (realized_vol + self.cfg.epsilon)
        return float(np.clip(raw, -self.cfg.z_clip, self.cfg.z_clip))

    def _ready(self, realized_vol: float) -> bool:
        """Warm-up only. The entry gate does its own finiteness check on ``er_rw``."""
        if self._n_returns < self.cfg.min_returns:
            return False
        if self.cfg.signal_mode == "vol_normalized" and not np.isfinite(realized_vol):
            return False
        return True

    def _refresh_R(self) -> None:
        if len(self._r_buf) < 2:
            return
        var = float(np.var(self._r_buf, ddof=1))
        self._R = max(var, self.cfg.r_floor)

    def _update_weakened(self, slope_z: float) -> None:
        """Latch when the post-entry drift fades; release when it comes back.

        Both halves matter. A one-way latch would put the whole rest of a leg
        into the tightened, hair-trigger window after the first ordinary
        pullback — every trend has one.
        """
        abs_z = abs(slope_z)
        self._peak_abs_z = max(self._peak_abs_z, abs_z)
        faded = abs_z < self.cfg.weaken_z or abs_z < self.cfg.weaken_frac * self._peak_abs_z
        self._weakened = bool(faded)

    def _current_er(self, prev_state: str) -> tuple[float, float, float]:
        """``(er_rw, signed_er_rw, n)`` — Kaufman ER in random-walk units."""
        cfg = self.cfg
        if (not cfg.anchor_er_to_regime) or prev_state == FLAT:
            if len(self._log_er) < cfg.er_window + 1:
                return float("nan"), float("nan"), float("nan")
            return _path_efficiency_rw(self._log_er, cfg.epsilon)
        prices = self._regime_log
        if self._weakened:
            prices = prices[-(cfg.er_tighten_window + 1) :]
        return _path_efficiency_rw(prices, cfg.epsilon)

    def _apply_state_machine(self, slope_z: float, er_rw: float, signed_er_rw: float) -> None:
        cfg = self.cfg
        if self._state == FLAT:
            er_ok = np.isfinite(er_rw) and er_rw >= cfg.er_entry_rw
            go_up = (
                er_ok
                and self._cusum_up >= cfg.cusum_h
                and slope_z >= cfg.resolved_up_entry_z
            )
            go_down = (
                er_ok
                and self._cusum_down >= cfg.cusum_h
                and slope_z <= -cfg.resolved_down_entry_z
            )
            if go_up and go_down:
                self._state = UP if slope_z >= 0.0 else DOWN
            elif go_up:
                self._state = UP
            elif go_down:
                self._state = DOWN
            return

        side = 1.0 if self._state == UP else -1.0
        opposite_cusum = self._cusum_down if self._state == UP else self._cusum_up
        if opposite_cusum >= cfg.reversal_h and side * slope_z < -cfg.reversal_z:
            self._state = DOWN if self._state == UP else UP
            return
        if cfg.anchor_er_to_regime and self._weakened and np.isfinite(er_rw):
            # The drift has faded, so judge the last few bars on their own.
            # Either way the answer is FLAT: an opposite trend must earn a
            # normal entry, or trip the reversal test above.
            if er_rw < cfg.er_hold_rw:
                self._state = FLAT
                return
            if np.isfinite(signed_er_rw) and side * signed_er_rw <= -cfg.er_flip_rw:
                self._state = FLAT
                return
        if side * slope_z < -cfg.exit_margin:
            self._adverse += 1
            if self._adverse >= cfg.exit_confirm_bars:
                self._state = FLAT
        else:
            self._adverse = 0


def replay_symbol(
    symbol: str,
    *,
    start: str | None = None,
    end: str | None = None,
    preset: str = "balanced",
    config: KalmanCUSUMConfig | Mapping[str, Any] | None = None,
    timeframe: str = "1Min",
    feed: str = "iex",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load clean bars with the canonical store and run the detector.

    Uses :func:`qtrader.data.ingest.load_clean_bars` — the same path everything
    else reads 1-minute data from.
    """
    from ..data.ingest import load_clean_bars

    bars = load_clean_bars(symbol, timeframe=timeframe, feed=feed, start=start, end=end)
    detector = (
        KalmanCUSUMRegimeDetector(config)
        if config is not None
        else KalmanCUSUMRegimeDetector.from_preset(preset)
    )
    return bars, detector.run(bars)


def _parse_bar(bar: pd.Series | Mapping[str, Any]) -> tuple[float, pd.Timestamp | None]:
    if isinstance(bar, pd.Series):
        if "close" not in bar.index:
            raise KeyError("bar must contain 'close'")
        close = float(bar["close"])
        ts: pd.Timestamp | None
        if isinstance(bar.name, pd.Timestamp):
            ts = bar.name
        elif "timestamp" in bar.index:
            ts = pd.Timestamp(bar["timestamp"])
        else:
            ts = None
        return close, ts
    if isinstance(bar, Mapping):
        if "close" not in bar:
            raise KeyError("bar must contain 'close'")
        close = float(bar["close"])
        raw = bar.get("timestamp")
        ts = pd.Timestamp(raw) if raw is not None else None
        return close, ts
    raise TypeError(f"bar must be a Series or mapping, got {type(bar)!r}")


def _session_keys(index: pd.Index) -> np.ndarray:
    """Session date per row, vectorised. ``run`` would otherwise pay for this per bar."""
    stamps = pd.DatetimeIndex(index)
    stamps = stamps.tz_localize("UTC") if stamps.tz is None else stamps.tz_convert("UTC")
    return session_date(stamps).to_numpy()


def _session_key(ts: pd.Timestamp) -> object:
    return _session_keys(pd.DatetimeIndex([pd.Timestamp(ts)]))[0]


def _std(buf: deque[float], window: int) -> float:
    if len(buf) < window:
        return float("nan")
    return float(np.std(buf, ddof=1))


def _path_efficiency_rw(log_prices, epsilon: float) -> tuple[float, float, float]:
    """Kaufman ER over ``n`` returns, rescaled so the null mean is 1.

    Under a driftless random walk ``E[|sum r| / sum|r|] = 1 / sqrt(n)`` exactly,
    so ``ER * sqrt(n)`` has mean 1.0 and sd 0.74 for every ``n``: one threshold
    works for the 10-bar entry window and the 5-bar tightened window alike.
    Returns ``(er_rw, signed_er_rw, n)``.
    """
    n = len(log_prices) - 1
    if n < 1:
        return float("nan"), float("nan"), float("nan")
    prices = np.fromiter(log_prices, dtype=float)
    net = float(prices[-1] - prices[0])
    travel = float(np.abs(np.diff(prices)).sum())
    scale = sqrt(n) / (travel + epsilon)
    return abs(net) * scale, net * scale, float(n)
