"""Trade episodes: the K-line window around every round trip, plus its context.

A backtest reports what a strategy earned. It does not say *when* the strategy
was right, and that is the question that turns a dead result into the next
hypothesis. An **episode** answers it for one trade:

* the price window — ``context_bars`` before the entry through the exit, so the
  setup and the outcome can be looked at as a chart;
* the market context measured **at the decision bar**, not at the fill: the
  strategy decided on bar ``t`` and was filled on ``t + execution_lag``, so
  conditioning on the fill bar would quietly use information the decision never
  had;
* the outcome, split into gross and net. Costs are near-constant per round trip,
  so "did the signal work" is a question about **gross** return, while "did the
  strategy make money" is a question about net. Conflating them hides whether a
  losing strategy has a real edge that is merely too small.

MFE/MAE (maximum favourable and adverse excursion) are recorded per episode
because the shape of a loss matters: a trade that never went the right way is a
wrong signal, while one that was up 40 bps before giving it all back is a
holding-period problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..runner import Run
from .features import FeatureSet, setup_features

#: Bars of price history kept before each entry.
DEFAULT_CONTEXT_BARS = 60

BPS = 1e4


@dataclass
class Episodes:
    """Labelled trade windows and their setup features."""

    #: One row per round trip, indexed by ``episode_id``.
    features: pd.DataFrame

    #: Long-format K-lines: one row per (episode_id, bar), ``offset`` counted
    #: from the entry bar, so negative offsets are the setup.
    bars: pd.DataFrame

    #: Which columns of ``features`` describe the setup rather than the outcome.
    #: Discovered from the run, so a strategy that exposes new quantities is
    #: screened on them without anything here changing.
    setup_columns: tuple[str, ...] = ()

    context_bars: int = DEFAULT_CONTEXT_BARS

    def __len__(self) -> int:
        return len(self.features)

    def window(self, episode_id: str) -> pd.DataFrame:
        """The K-lines of one episode, indexed by timestamp."""
        window = self.bars.loc[self.bars["episode_id"] == episode_id]
        return window.set_index("timestamp").drop(columns=["episode_id"])

    def best(self, n: int = 8, by: str = "gross_return_bps") -> pd.DataFrame:
        return self.features.nlargest(n, by)

    def worst(self, n: int = 8, by: str = "gross_return_bps") -> pd.DataFrame:
        return self.features.nsmallest(n, by)

    # ------------------------------------------------------------- analysis
    def screen(self, *, target: str = "gross_return_bps", n_bins: int = 5) -> pd.DataFrame:
        """Rank every setup feature by its apparent relationship to the outcome."""
        from .conditions import rank_conditions

        return rank_conditions(self.features, self.setup_columns, target=target, n_bins=n_bins)

    def contrast(self, *, outcome: str = "gross_win") -> pd.DataFrame:
        """Compare winners against losers on every setup feature."""
        from .conditions import outcome_contrast

        return outcome_contrast(self.features, self.setup_columns, outcome=outcome)

    def profile(self, feature: str, *, target: str = "gross_return_bps", n_bins: int = 5):
        """Mean outcome per quantile bin of one setup feature."""
        from .conditions import quantile_profile

        return quantile_profile(self.features, feature, target=target, n_bins=n_bins)

    def threshold(self, alpha: float = 0.05) -> float:
        """|t| a feature must clear, given how many were screened."""
        from .conditions import bonferroni_t_threshold

        return bonferroni_t_threshold(len(self.setup_columns), alpha)

    # ---------------------------------------------------------------- storage
    def save(self, directory: Path | str) -> Path:
        """Persist the tables and the column roles, so an analysis can be rerun
        without a backtest and without guessing which columns were the setup."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.features.to_parquet(directory / "episode_features.parquet")
        self.bars.to_parquet(directory / "episode_bars.parquet", index=False)
        (directory / "episodes.json").write_text(
            json.dumps(
                {"setup_columns": list(self.setup_columns), "context_bars": self.context_bars},
                indent=2,
            )
        )
        return directory

    @classmethod
    def load(cls, directory: Path | str) -> "Episodes":
        directory = Path(directory)
        manifest_path = directory / "episodes.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        return cls(
            features=pd.read_parquet(directory / "episode_features.parquet"),
            bars=pd.read_parquet(directory / "episode_bars.parquet"),
            setup_columns=tuple(manifest.get("setup_columns", ())),
            context_bars=manifest.get("context_bars", DEFAULT_CONTEXT_BARS),
        )


def extract_episodes(
    run: Run,
    *,
    context_bars: int = DEFAULT_CONTEXT_BARS,
    features: FeatureSet | None = None,
) -> Episodes:
    """Build one episode per completed round trip in ``run``.

    ``features`` defaults to the universal market features plus whatever the
    strategy exposes through
    :meth:`qtrader.strategies.base.Strategy.setup_features`, so this works on
    any strategy without configuration. Pass a custom set to screen against
    something else.
    """
    trades = run.result.trades
    features = features or setup_features(run.strategy, run.result.signals, run.context)

    if trades.empty:
        return Episodes(
            features=pd.DataFrame(),
            bars=pd.DataFrame(),
            setup_columns=features.names,
            context_bars=context_bars,
        )

    panel = run.context.panel
    position = pd.Series(np.arange(len(panel.index)), index=panel.index)
    lag = run.config.execution.execution_lag_bars

    windows = []
    rows = []

    for episode_id, trade in _identified(trades):
        entry_pos = int(position.loc[trade["entry_time"]])
        exit_pos = int(position.loc[trade["exit_time"]])
        symbol = trade["symbol"]

        window = _window(panel, run, symbol, entry_pos, exit_pos, context_bars)
        window.insert(0, "episode_id", episode_id)
        windows.append(window)

        rows.append(
            {
                "episode_id": episode_id,
                **_describe_trade(trade, entry_pos, exit_pos),
                # The decision bar, never the fill: conditioning on the fill
                # would use information the strategy did not have.
                **features.sample(symbol, max(entry_pos - lag, 0)),
                **_excursions(window, trade),
            }
        )

    table = pd.DataFrame(rows).set_index("episode_id")
    table["sector"] = table["symbol"].map(run.context.universe.sectors)
    return Episodes(
        features=table,
        bars=pd.concat(windows, ignore_index=True),
        setup_columns=features.names,
        context_bars=context_bars,
    )


def _identified(trades: pd.DataFrame):
    """Yield ``(episode_id, trade)`` with ids stable across reruns and unique.

    A partial close or a reversal can produce two round trips in the same symbol
    from the same entry minute, so the natural key needs a tie-break suffix —
    otherwise the second one silently overwrites the first when the features are
    indexed by id.
    """
    seen: dict[str, int] = {}
    for _, trade in trades.sort_values(["entry_time", "symbol", "exit_time"]).iterrows():
        stamp = pd.Timestamp(trade["entry_time"]).strftime("%Y%m%dT%H%M")
        key = f"{trade['symbol']}_{stamp}_{trade['direction'][0]}"
        seen[key] = seen.get(key, 0) + 1
        yield (key if seen[key] == 1 else f"{key}#{seen[key]}"), trade


def _describe_trade(trade: pd.Series, entry_pos: int, exit_pos: int) -> dict:
    notional = abs(trade["shares"]) * trade["entry_reference"]
    return {
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "entry_time": trade["entry_time"],
        "exit_time": trade["exit_time"],
        "hold_bars": exit_pos - entry_pos,
        "notional": notional,
        "gross_return_bps": trade["gross_pnl"] / notional * BPS,
        "net_return_bps": trade["net_pnl"] / notional * BPS,
        "cost_bps": trade["costs"] / notional * BPS,
        "gross_win": bool(trade["gross_pnl"] > 0),
        "net_win": bool(trade["net_pnl"] > 0),
    }


def _window(run_panel, run: Run, symbol: str, entry_pos: int, exit_pos: int, context_bars: int):
    """K-lines from ``context_bars`` before the entry through the exit."""
    start = max(entry_pos - context_bars, 0)
    stop = exit_pos + 1
    bars = run_panel.bars(symbol)
    index = run_panel.index[start:stop]

    window = bars.reindex(index).copy()
    window.insert(0, "offset", np.arange(start, stop) - entry_pos)

    # Carry the strategy's own indicator lines when it has them, so the episode
    # chart shows what the rule was looking at rather than bare candles.
    indicators = run.result.signals.indicators.get(symbol)
    if indicators is not None:
        for column in indicators.columns:
            window[column] = indicators[column].reindex(index).to_numpy()
    return window.reset_index()


def _excursions(window: pd.DataFrame, trade: pd.Series) -> dict:
    """Best and worst the trade ever looked, between entry and exit."""
    holding = window.loc[window["offset"] >= 0]
    entry_price = trade["entry_reference"]
    sign = 1.0 if trade["direction"] == "LONG" else -1.0

    favourable = sign * (holding["high" if sign > 0 else "low"] / entry_price - 1.0)
    adverse = sign * (holding["low" if sign > 0 else "high"] / entry_price - 1.0)
    return {
        "mfe_bps": float(favourable.max() * BPS),
        "mae_bps": float(adverse.min() * BPS),
    }


def oriented_paths(episodes: Episodes) -> pd.DataFrame:
    """Every episode's price path in bps from its entry, signed so up is profit.

    Long-format: ``episode_id, offset, path_bps`` joined to the outcome flags.
    Averaging these by ``offset`` is the clearest picture of what a working
    setup and a failing one actually do — before the entry as well as after it,
    which is where a mistimed signal shows up.
    """
    if not len(episodes):
        return pd.DataFrame(columns=["episode_id", "offset", "path_bps"])

    features = episodes.features
    entry_price = (
        episodes.bars.loc[episodes.bars["offset"] == 0]
        .set_index("episode_id")["open"]
        .rename("entry_price")
    )
    sign = features["direction"].map({"LONG": 1.0, "SHORT": -1.0}).rename("sign")

    paths = episodes.bars[["episode_id", "offset", "close"]].join(
        pd.concat([entry_price, sign, features[["gross_win", "net_win"]]], axis=1),
        on="episode_id",
    )
    paths["path_bps"] = (paths["close"] / paths["entry_price"] - 1.0) * BPS * paths["sign"]
    return paths.drop(columns=["close", "entry_price", "sign"])


def excursion_summary(episodes: Episodes) -> pd.DataFrame:
    """How wins and losses got where they ended up.

    The gap between the realised return and the excursions is the tell: if a
    typical loser spent time well in profit, the signal was not necessarily
    wrong — the exit was. If it never went the right way, the signal was.
    """
    features = episodes.features
    rows = []
    for label, subset in (
        ("gross winners", features.loc[features["gross_win"]]),
        ("gross losers", features.loc[~features["gross_win"]]),
    ):
        if subset.empty:
            continue
        rows.append(
            {
                "group": label,
                "n": len(subset),
                "mean_gross_bps": subset["gross_return_bps"].mean(),
                "mean_mfe_bps": subset["mfe_bps"].mean(),
                "mean_mae_bps": subset["mae_bps"].mean(),
                "mean_hold_bars": subset["hold_bars"].mean(),
                "share_ever_favourable": float((subset["mfe_bps"] > 5).mean()),
                "share_ever_adverse": float((subset["mae_bps"] < -5).mean()),
            }
        )
    return pd.DataFrame(rows)
