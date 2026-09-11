"""Causal local-linear-trend Kalman *filter*, with a random-walk null scale.

State ``x = [level, slope]``. One predict+update per closed bar. There is no
smoother, no RTS pass, no centred window. ``Q`` defaults to a diagonal scaled
by the current causal measurement variance ``R``; ``process_noise_covariance``
is the extension point for a discrete white-noise-acceleration (constant
velocity) form.

Two scales for the slope
------------------------
``slope_std`` is ``sqrt(P[1, 1])``: the posterior uncertainty **under the
model the filter assumes**. It is only as good as that model, and this model is
misspecified for minute log prices — the residual innovations are neither white
nor unit-variance, and no ``Q/R`` ratio in this family makes them so.
Standardising by it produces a "z-score" whose spread varies by a factor of two
across symbols, so a threshold tuned on one name means something else on
another.

``slope_rw_std`` is the honest alternative, and the same one the rest of the
repo uses (``features.trend``, ``features.momentum``): the sampling scale of
the estimator **under a driftless random walk**. The filter is linear, so with
``M_t = (I - K_t H) F`` the state obeys ``x_t = M_t x_{t-1} + K_t y_t`` and the
measurement obeys ``y_t = y_{t-1} + r_t``. Stacking ``z = (level, slope, y)``,

    z_t = A_t z_{t-1} + b_t r_t,    A_t = [[M_t, K_t], [0, 0, 1]],  b_t = (K_t, 1)

so under ``r_t ~ (0, s_t^2)`` independent the null covariance follows a
Lyapunov recursion ``V_t = A_t V_{t-1} A_t' + s_t^2 b_t b_t'`` and
``slope_rw_std = sqrt(V_t[1, 1])``. It is exact (verified against Monte Carlo),
costs one 3x3 product per bar, tracks a time-varying ``K_t``, and carries the
session-restart transient for free — ``||a_t||`` really is smaller in the first
minutes of a session because fewer shocks have had time to contribute.

``null_var`` is a separate argument from ``R`` on purpose: ``R`` is what the
filter *assumes* about observation noise, ``null_var`` is what the *null* says
about one bar's return variance. They coincide today; a time-of-day volatility
profile would change the second without touching the first.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

F = np.array([[1.0, 1.0], [0.0, 1.0]], dtype=float)
H = np.array([1.0, 0.0], dtype=float)
I2 = np.eye(2)


class FilterStep(NamedTuple):
    """One bar of filter output. ``slope_rw_std`` is the scale to standardise by."""

    level: float
    slope: float
    slope_std: float
    slope_rw_std: float


def process_noise_covariance(
    kind: str, q_level: float, q_slope: float, *, dt: float = 1.0
) -> np.ndarray:
    """Return the 2x2 process-noise ``Q`` for one bar.

    * ``diagonal`` — independent level / slope jitter. First-version default.
    * ``acceleration`` / ``constant_velocity`` — discrete white-noise
      acceleration on a constant-velocity kinematic model (bar time ``dt=1``),
      plus ``q_level`` on the level so ``lambda_level`` still has an effect.
    """
    if kind == "diagonal":
        return np.array([[q_level, 0.0], [0.0, q_slope]], dtype=float)
    if kind in ("acceleration", "constant_velocity"):
        dt2 = dt * dt
        q = np.array(
            [
                [dt2 * dt2 / 4.0, dt2 * dt / 2.0],
                [dt2 * dt / 2.0, dt2],
            ],
            dtype=float,
        ) * q_slope
        q[0, 0] += q_level
        return q
    raise ValueError(f"unknown process-noise kind {kind!r}")


class LocalLinearTrendFilter:
    """Two-state Kalman filter. Call :meth:`step` once per bar with that bar's ``y`` and ``R``."""

    def __init__(
        self,
        *,
        lambda_level: float = 0.01,
        lambda_slope: float = 0.001,
        process_noise: str = "diagonal",
        epsilon: float = 1e-8,
    ) -> None:
        self.lambda_level = float(lambda_level)
        self.lambda_slope = float(lambda_slope)
        self.process_noise = process_noise
        self.epsilon = float(epsilon)
        self.x = np.zeros(2, dtype=float)
        self.P = np.eye(2)
        self.V = np.zeros((3, 3), dtype=float)

    def reset(self, y: float, R: float) -> None:
        """Prior at a session (or stream) start: level = ``y``, slope = 0.

        The null covariance restarts at zero: the anchor is observed, not
        estimated, so no random-walk shock has entered the state yet.
        """
        r = max(float(R), self.epsilon)
        self.x = np.array([float(y), 0.0], dtype=float)
        self.P = np.diag([10.0 * r, r])
        self.V = np.zeros((3, 3), dtype=float)

    def q_matrices(self, R: float) -> tuple[float, np.ndarray]:
        r = max(float(R), self.epsilon)
        q_level = self.lambda_level * r
        q_slope = self.lambda_slope * r
        return r, process_noise_covariance(self.process_noise, q_level, q_slope)

    def step(self, y: float, R: float, *, null_var: float | None = None) -> FilterStep:
        """Predict, update, and advance the random-walk null covariance.

        ``null_var`` is the variance of one bar's return under the driftless
        null; it defaults to ``R``.
        """
        r, Q = self.q_matrices(R)
        x_pred = F @ self.x
        P_pred = F @ self.P @ F.T + Q
        P_pred = _symmetrize(P_pred)

        innovation = float(y) - float(H @ x_pred)
        s = float(H @ P_pred @ H) + r
        s = max(s, self.epsilon)
        K = (P_pred @ H) / s
        self.x = x_pred + K * innovation
        ikh = I2 - np.outer(K, H)
        self.P = ikh @ P_pred @ ikh.T + np.outer(K, K) * r
        self.P = _symmetrize(self.P)
        self.P[0, 0] = max(self.P[0, 0], self.epsilon)
        self.P[1, 1] = max(self.P[1, 1], self.epsilon)

        self._advance_null(ikh @ F, K, r if null_var is None else max(float(null_var), 0.0))

        return FilterStep(
            float(self.x[0]),
            float(self.x[1]),
            float(np.sqrt(self.P[1, 1])),
            self.slope_rw_std,
        )

    def _advance_null(self, M: np.ndarray, K: np.ndarray, null_var: float) -> None:
        """``V <- A V A' + null_var * b b'`` for ``z = (level, slope, y)``."""
        A = np.zeros((3, 3), dtype=float)
        A[:2, :2] = M
        A[:2, 2] = K
        A[2, 2] = 1.0
        b = np.array([K[0], K[1], 1.0], dtype=float)
        self.V = _symmetrize(A @ self.V @ A.T + null_var * np.outer(b, b))

    @property
    def level(self) -> float:
        return float(self.x[0])

    @property
    def slope(self) -> float:
        return float(self.x[1])

    @property
    def slope_std(self) -> float:
        return float(np.sqrt(max(self.P[1, 1], self.epsilon)))

    @property
    def slope_rw_std(self) -> float:
        """Sampling scale of ``slope`` under a driftless random walk."""
        return float(np.sqrt(max(self.V[1, 1], self.epsilon)))


def _symmetrize(P: np.ndarray) -> np.ndarray:
    return 0.5 * (P + P.T)
