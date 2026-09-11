"""Two-sided CUSUM on a causal scalar signal.

``S+`` accumulates evidence of a positive shift; ``S-`` accumulates evidence
of a negative shift and is stored as a **non-negative** amount of down-move
evidence, never as a negative running sum.
"""

from __future__ import annotations


def update_cusum(
    s_plus: float, s_minus: float, signal: float, k: float
) -> tuple[float, float]:
    """One CUSUM step. Both outputs are ``>= 0``."""
    up = max(0.0, s_plus + signal - k)
    down = max(0.0, s_minus - signal - k)
    return up, down
