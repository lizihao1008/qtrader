"""Presets and knobs for the Kalman–CUSUM regime detector.

Every gate here is expressed in **driftless-random-walk units**, the same null
the rest of the repo standardises against (CONTEXT §7b):

* ``slope_z`` is the Kalman slope over its sampling scale under that null, so
  it is standard normal when nothing is happening. ``entry_z = 2`` means "the
  drift is larger than 95% of what noise alone produces", on any symbol, at any
  time of day, at any price level.
* ``er_*_rw`` are Kaufman efficiency ratios multiplied by ``sqrt(n)``. Under the
  null ``E[ER_n] = 1/sqrt(n)`` exactly, so ``ER * sqrt(n)`` has mean 1.0 and
  sd 0.74 **whatever the window length is**: 1.0 reads "as directional as
  noise", 2.0 is roughly the null's 90th percentile. Raw ``ER`` thresholds do
  not have this property — ``ER >= 0.25`` is a 55% coin flip on a 10-bar window
  and a 75% one on a 5-bar window.

``cusum_k`` / ``cusum_h`` are the one pair that is *not* a closed-form null
quantity: the CUSUM input is a filtered, strongly autocorrelated series, so the
classical i.i.d. ARL calibration does not apply. They are persistence knobs, and
:func:`qtrader.regime.evaluate.null_entry_rate` measures what they actually cost
by replaying the detector over simulated driftless random walks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

SIGNAL_MODES = ("kalman_z", "vol_normalized")
PROCESS_NOISE_KINDS = ("diagonal", "acceleration", "constant_velocity")
PRESET_NAMES = ("sensitive", "balanced", "conservative")


def default_config() -> dict:
    """The balanced preset as a plain dict (the recommended starting point)."""
    return KalmanCUSUMConfig().to_dict()


@dataclass(frozen=True)
class KalmanCUSUMConfig:
    """Causal detector parameters. ``from_preset`` / ``from_dict`` are the constructors."""

    # Kalman local-linear-trend: Q = diag(lambda * R, ...) by default
    lambda_level: float = 0.01
    lambda_slope: float = 0.001
    process_noise: str = "diagonal"

    # slope_z = slope / slope_rw_std (standard normal under a driftless RW)
    z_clip: float = 5.0
    epsilon: float = 1e-8

    # causal measurement variance R (also the per-bar null variance for now)
    r_window: int = 40
    r_floor: float = 1e-10
    min_returns: int = 10

    # realized vol (used when signal_mode == "vol_normalized")
    vol_window: int = 20
    signal_mode: str = "kalman_z"

    # two-sided CUSUM on the chosen signal — a persistence knob, not an ARL
    cusum_k: float = 0.5
    cusum_h: float = 2.5

    # Kaufman ER on log close, session-bounded, in random-walk units (ER * sqrt(n))
    er_window: int = 10
    er_entry_rw: float = 2.0

    # FLAT → trend
    entry_z: float = 2.0
    up_entry_z: float | None = None
    down_entry_z: float | None = None

    # hysteresis: trend → FLAT. UP leaves when slope_z < -exit_margin, DOWN when
    # slope_z > +exit_margin, for exit_confirm_bars in a row. Larger = stickier.
    exit_margin: float = 0.5
    exit_confirm_bars: int = 5

    # After entry, ER (and the weaken check) only use bars of *this* regime.
    # A long UP must not keep a high ER because yesterday's sell-off is still
    # in a fixed rolling window. When slope_z fades the lookback shrinks to
    # ``er_tighten_window``; a chopped or cleanly retraced window ends the
    # trend, and the *opposite* trend then has to earn its own entry.
    anchor_er_to_regime: bool = True
    er_tighten_window: int = 5
    weaken_z: float = 0.75
    weaken_frac: float = 0.5
    er_hold_rw: float = 0.4
    er_flip_rw: float = 2.0

    # trend → opposite trend, the only direct UP↔DOWN path
    reversal_h: float = 5.0
    reversal_z: float = 2.0

    reset_on_session: bool = True

    def __post_init__(self) -> None:
        if self.lambda_level < 0 or self.lambda_slope < 0:
            raise ValueError("lambda_level and lambda_slope must be >= 0")
        if self.process_noise not in PROCESS_NOISE_KINDS:
            raise ValueError(
                f"process_noise must be one of {PROCESS_NOISE_KINDS}, got {self.process_noise!r}"
            )
        if self.z_clip <= 0:
            raise ValueError("z_clip must be positive")
        if self.epsilon <= 0:
            raise ValueError("epsilon must be positive")
        if self.r_window < 2:
            raise ValueError("r_window must be at least 2")
        if self.r_floor <= 0:
            raise ValueError("r_floor must be positive")
        if self.min_returns < 2:
            raise ValueError("min_returns must be at least 2")
        if self.vol_window < 2:
            raise ValueError("vol_window must be at least 2")
        if self.signal_mode not in SIGNAL_MODES:
            raise ValueError(f"signal_mode must be one of {SIGNAL_MODES}")
        if self.cusum_k < 0:
            raise ValueError("cusum_k must be >= 0")
        if self.cusum_h <= 0:
            raise ValueError("cusum_h must be positive")
        if self.er_window < 2:
            raise ValueError("er_window must be at least 2")
        if self.er_entry_rw < 0:
            raise ValueError("er_entry_rw must be >= 0")
        if self.entry_z < 0:
            raise ValueError("entry_z must be >= 0")
        if self.up_entry_z is not None and self.up_entry_z < 0:
            raise ValueError("up_entry_z must be >= 0")
        if self.down_entry_z is not None and self.down_entry_z < 0:
            raise ValueError("down_entry_z must be >= 0")
        if self.exit_margin < 0:
            raise ValueError("exit_margin must be >= 0; larger means a stickier trend")
        if self.exit_confirm_bars < 1:
            raise ValueError("exit_confirm_bars must be at least 1")
        if self.er_tighten_window < 2:
            raise ValueError("er_tighten_window must be at least 2")
        if self.weaken_z < 0:
            raise ValueError("weaken_z must be >= 0")
        if not 0.0 <= self.weaken_frac <= 1.0:
            raise ValueError("weaken_frac must lie in [0, 1]")
        if self.er_hold_rw < 0:
            raise ValueError("er_hold_rw must be >= 0")
        if self.er_flip_rw < 0:
            raise ValueError("er_flip_rw must be >= 0")
        if self.reversal_h <= 0 or self.reversal_z < 0:
            raise ValueError("reversal_h must be positive and reversal_z >= 0")

    @property
    def resolved_up_entry_z(self) -> float:
        return self.entry_z if self.up_entry_z is None else self.up_entry_z

    @property
    def resolved_down_entry_z(self) -> float:
        return self.entry_z if self.down_entry_z is None else self.down_entry_z

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "KalmanCUSUMConfig":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    @classmethod
    def from_preset(cls, name: str) -> "KalmanCUSUMConfig":
        key = name.lower()
        if key not in PRESETS:
            raise KeyError(f"unknown preset {name!r}; known: {PRESET_NAMES}")
        return PRESETS[key]

    def replace(self, **overrides) -> "KalmanCUSUMConfig":
        data = self.to_dict()
        data.update(overrides)
        return KalmanCUSUMConfig.from_dict(data)


# Thresholds are null quantiles, not a fit. The three presets trade detection
# delay for false entries; `evaluate.null_entry_rate` reports what each costs
# on simulated driftless random walks.
PRESETS: dict[str, KalmanCUSUMConfig] = {
    "balanced": KalmanCUSUMConfig(),
    "sensitive": KalmanCUSUMConfig(
        lambda_slope=0.003,
        cusum_h=1.5,
        entry_z=1.5,
        er_entry_rw=1.5,
        exit_margin=0.25,
        exit_confirm_bars=3,
        reversal_h=2.0,
        reversal_z=1.5,
    ),
    "conservative": KalmanCUSUMConfig(
        lambda_slope=0.0003,
        cusum_h=4.0,
        entry_z=2.5,
        er_entry_rw=2.5,
        exit_margin=0.75,
        exit_confirm_bars=8,
        reversal_h=6.0,
        reversal_z=2.5,
    ),
}
