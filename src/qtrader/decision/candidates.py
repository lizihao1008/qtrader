"""Finding the entries a strategy proposed, without asking the strategy.

A candidate is a bar at which a symbol's target weight starts a new position:
flat becoming non-flat, or a direct reversal. That is computable from
`target_weights` alone, so every registered strategy is supported without any of
them publishing anything new.

The unit is a **position run** — the candidate bar plus every bar the position
is held — because a veto has to remove the whole holding, not just the entry.
Zeroing only the entry bar would leave the following bars non-zero and the
engine would simply open the position one bar later.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Candidate:
    """One proposed position: where it starts, which way, and how long it runs."""

    timestamp: pd.Timestamp
    symbol: str
    direction: int          # +1 long, -1 short
    position: int           # row index of the decision bar
    end_position: int       # row index of the last bar held (inclusive)
    weight: float           # what the strategy asked for, for the record only

    @property
    def hold_bars(self) -> int:
        return self.end_position - self.position + 1


def find_candidates(target_weights: pd.DataFrame) -> list[Candidate]:
    """Every position run in a weight frame, in chronological order."""
    index = target_weights.index
    found: list[Candidate] = []

    for symbol in target_weights.columns:
        signs = np.sign(target_weights[symbol].to_numpy(dtype=float))
        signs = np.nan_to_num(signs)
        # A run starts wherever the sign changes to something non-zero: flat to
        # long, flat to short, and long straight to short (a reversal is a new
        # position, not a continuation).
        previous = np.concatenate([[0.0], signs[:-1]])
        starts = np.flatnonzero((signs != 0) & (signs != previous))

        for start in starts:
            end = start
            while end + 1 < len(signs) and signs[end + 1] == signs[start]:
                end += 1
            found.append(
                Candidate(
                    timestamp=index[start],
                    symbol=symbol,
                    direction=int(signs[start]),
                    position=int(start),
                    end_position=int(end),
                    weight=float(target_weights[symbol].iat[start]),
                )
            )

    return sorted(found, key=lambda c: (c.position, c.symbol))


def apply_vetoes(target_weights: pd.DataFrame, vetoed: list[Candidate]) -> pd.DataFrame:
    """A copy of the weights with each vetoed position run zeroed.

    Only ever removes exposure. The result satisfies ``sum(|w|) <= 1`` whenever
    the input did, because every element either keeps its value or becomes zero.
    """
    if not vetoed:
        return target_weights

    columns = {name: i for i, name in enumerate(target_weights.columns)}
    # `to_numpy` can hand back a read-only view of the frame's own block.
    values = np.array(target_weights.to_numpy(dtype=float), copy=True)
    for candidate in vetoed:
        values[candidate.position : candidate.end_position + 1, columns[candidate.symbol]] = 0.0
    return pd.DataFrame(values, index=target_weights.index, columns=target_weights.columns)
