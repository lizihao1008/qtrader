"""One call from a config to a finished win/loss diagnosis.

The whole point of this module is that adding a strategy should not mean adding
analysis code. Everything below works off the `Strategy` interface, so::

    from qtrader.analysis import diagnose

    diagnosis = diagnose("config/backtest/my_new_strategy.yaml", split="mine")
    print(diagnosis.summary())
    diagnosis.write_report()

runs the backtest, extracts one episode per round trip, screens every setup
feature against the outcome and writes the report — for a strategy this module
has never seen. A strategy that overrides
:meth:`qtrader.strategies.base.Strategy.setup_features` also gets its own
quantities screened; one that does not is still screened against the universal
market features.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd

from ..config import RunConfig
from ..data.storage import BarStore
from ..experiments.splits import DEFAULT_SPLITS_PATH, Split, apply_split, load_splits
from ..runner import Run, execute
from .episodes import DEFAULT_CONTEXT_BARS, Episodes, extract_episodes


@dataclass
class Diagnosis:
    """A completed run, its episodes, and the screens over them."""

    run: Run
    episodes: Episodes
    split: Split | None = None

    @cached_property
    def conditions(self) -> pd.DataFrame:
        """Setup features ranked by their apparent link to gross return."""
        return self.episodes.screen()

    @cached_property
    def contrast(self) -> pd.DataFrame:
        """Winners vs losers, feature by feature."""
        return self.episodes.contrast()

    @property
    def threshold(self) -> float:
        """|t| a feature must clear, given how many were screened."""
        return self.episodes.threshold()

    @property
    def survivors(self) -> pd.DataFrame:
        """Features that clear the multiple-testing bar.

        Being here is not evidence — it means a candidate is worth confirming on
        a window it was not found on.
        """
        if self.conditions.empty:
            return self.conditions
        return self.conditions.loc[self.conditions["t_rho"].abs() > self.threshold]

    def summary(self) -> str:
        """A few lines that say whether anything was found."""
        features = self.episodes.features
        lines = [
            f"{self.run.config.run_id}: {len(self.episodes)} round trips"
            + (f" · {self.split.describe()}" if self.split else ""),
            f"  gross win rate {features['gross_win'].mean():.1%}"
            f" · mean gross {features['gross_return_bps'].mean():+.2f} bps"
            f" · mean cost {features['cost_bps'].mean():.2f} bps",
            f"  screened {len(self.episodes.setup_columns)} setup features,"
            f" |t| must exceed {self.threshold:.2f}",
        ]
        if self.survivors.empty:
            strongest = (
                self.conditions.iloc[0] if not self.conditions.empty else None
            )
            detail = (
                f" (strongest: {strongest['feature']} at t={strongest['t_rho']:+.2f})"
                if strongest is not None
                else ""
            )
            lines.append(f"  no feature clears the bar{detail}")
        else:
            for _, row in self.survivors.iterrows():
                lines.append(
                    f"  CANDIDATE {row['feature']}: t={row['t_rho']:+.2f},"
                    f" bin spread {row['bin_spread']:+.2f} bps,"
                    f" {'monotone' if row['monotone'] else 'NOT monotone'}"
                    " — confirm on another window"
                )
        return "\n".join(lines)

    def output_dir(self) -> Path:
        return self.run.config.output_dir() / "episodes"

    def save(self, directory: Path | str | None = None) -> Path:
        """Persist the episode tables for later reanalysis."""
        return self.episodes.save(directory or self.output_dir())

    def write_report(self, path: Path | str | None = None, *, gallery_size: int = 6) -> Path:
        """Render the HTML report: paths, excursions, screens and a gallery."""
        from ..viz.episodes import write_episode_report

        return write_episode_report(
            self.episodes,
            self.run,
            path or (self.output_dir() / "episodes.html"),
            split=self.split,
            gallery_size=gallery_size,
        )


def diagnose(
    config: RunConfig | Path | str,
    *,
    split: str | Split | None = None,
    splits_file: Path | str = DEFAULT_SPLITS_PATH,
    overrides: dict | None = None,
    context_bars: int = DEFAULT_CONTEXT_BARS,
    store: BarStore | None = None,
    run: Run | None = None,
) -> Diagnosis:
    """Run a strategy and diagnose which of its trades worked, and why.

    ``config`` may be a :class:`~qtrader.config.RunConfig` or a path to one.
    ``split`` restricts the window to a named entry in ``config/splits.yaml``.
    ``run`` reuses an already-executed backtest instead of running it again.
    """
    resolved = config if isinstance(config, RunConfig) else RunConfig.from_yaml(config)
    chosen = _resolve_split(split, splits_file)
    if chosen is not None:
        resolved = apply_split(resolved, chosen)
    if overrides:
        resolved = resolved.with_overrides(overrides)

    executed = run or execute(resolved, store)
    return Diagnosis(
        run=executed,
        episodes=extract_episodes(executed, context_bars=context_bars),
        split=chosen,
    )


def _resolve_split(split: str | Split | None, splits_file: Path | str) -> Split | None:
    if split is None or isinstance(split, Split):
        return split
    splits = load_splits(splits_file)
    if split not in splits:
        raise KeyError(f"unknown split {split!r}; available: {sorted(splits)}")
    return splits[split]
