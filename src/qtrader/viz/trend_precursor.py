"""PNG charts for the trend-precursor study."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd

LAGS = tuple(range(-30, 0))


def write_precursor_charts(
    output_dir: Path,
    samples: pd.DataFrame,
    stats: pd.DataFrame,
    event_time: pd.DataFrame,
    *,
    windows: tuple[int, ...],
    leads: tuple[int, ...],
) -> list[Path]:
    charts = Path(output_dir) / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    written = [
        _event_time_chart(
            charts / "event_time_up.png",
            event_time, "up", "Average mom_5",
            "UP trends vs matched controls — mom_5",
        ),
        _event_time_chart(
            charts / "event_time_down.png",
            event_time, "down", "Average mom_5",
            "DOWN trends vs matched controls — mom_5",
        ),
        _event_time_chart(
            charts / "event_time_directional.png",
            event_time, "directional", "Average direction × mom_5",
            "All trends vs controls — direction × mom_5",
        ),
        _auc_heatmap(charts / "auc_heatmap.png", stats, windows, leads),
    ]
    deciles = stats.loc[
        (stats["side"] == "directional") & (stats["window"] == 5)
    ]
    for _, row in deciles.iterrows():
        lead = int(row["lead"])
        written.append(
            _decile_chart(
                charts / f"decile_mom5_lead{lead}.png",
                samples, window=5, lead=lead,
            )
        )
    return written


def _event_time_chart(
    path: Path, table: pd.DataFrame, side: str, ylabel: str, title: str
) -> Path:
    block = table.loc[table["side"] == side]
    fig, ax = plt.subplots(figsize=(8.2, 4.4), dpi=120)
    for kind, colour, label in (
        ("trend", "#1565c0", "Trend"),
        ("control", "#90a4ae", "Control"),
    ):
        part = block.loc[block["sample_type"] == kind].sort_values("lag")
        if part.empty:
            continue
        ax.plot(part["lag"], part["mean"], color=colour, lw=2.0, label=label)
        if {"lo", "hi"}.issubset(part.columns):
            ax.fill_between(
                part["lag"], part["lo"], part["hi"],
                color=colour, alpha=0.18, linewidth=0,
            )
    ax.axhline(0.0, color="#b0bec5", lw=0.8)
    ax.axvline(-1, color="#b0bec5", lw=0.8, ls=":")
    ax.set_xlim(-30, -1)
    ax.set_xlabel("Minutes before event")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _auc_heatmap(
    path: Path,
    stats: pd.DataFrame,
    windows: tuple[int, ...],
    leads: tuple[int, ...],
) -> Path:
    block = stats.loc[stats["side"] == "directional"]
    grid = np.full((len(windows), len(leads)), np.nan)
    for i, window in enumerate(windows):
        for j, lead in enumerate(leads):
            hit = block.loc[(block["window"] == window) & (block["lead"] == lead)]
            if not hit.empty:
                grid[i, j] = float(hit.iloc[0]["auc"])
    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=120)
    mesh = ax.imshow(grid, cmap="RdBu_r", vmin=0.35, vmax=0.65, origin="upper")
    ax.set_xticks(range(len(leads)), [str(v) for v in leads])
    ax.set_yticks(range(len(windows)), [str(v) for v in windows])
    ax.set_xlabel("lead (minutes before event bar)")
    ax.set_ylabel("momentum window")
    ax.set_title("ROC AUC  (directional momentum vs is_trend)")
    for i in range(len(windows)):
        for j in range(len(leads)):
            value = grid[i, j]
            if np.isfinite(value):
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=9)
    fig.colorbar(mesh, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _decile_chart(path: Path, samples: pd.DataFrame, *, window: int, lead: int) -> Path:
    col = f"dmom_{window}_lead{lead}"
    if col not in samples.columns:
        col = f"mom_{window}_lead{lead}"
        frame = samples[["is_trend", col]].dropna().copy()
        frame["factor"] = frame[col] * samples.loc[frame.index, "direction"]
    else:
        frame = samples[["is_trend", col]].dropna().rename(columns={col: "factor"})
    if frame.empty:
        fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=120)
        ax.set_title(f"mom_{window} lead {lead} — no finite samples")
        fig.savefig(path)
        plt.close(fig)
        return path
    frame["bucket"] = _decile_bucket(frame["factor"])
    rates = frame.groupby("bucket")["is_trend"].mean()
    baseline = float(frame["is_trend"].mean())
    fig, ax = plt.subplots(figsize=(6.8, 3.8), dpi=120)
    ax.bar(rates.index + 1, rates.to_numpy(), color="#1565c0", width=0.8)
    ax.axhline(baseline, color="#ef6c00", lw=1.4, ls="--", label=f"baseline {baseline:.2f}")
    ax.set_xticks(range(1, 11))
    ax.set_xlabel("Factor decile (directional momentum, low → high)")
    ax.set_ylabel("P(Trend)")
    ax.set_title(f"mom_{window} lead {lead}")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def _decile_bucket(values: pd.Series) -> pd.Series:
    pct = values.rank(method="average", pct=True)
    return np.minimum((pct * 10).astype(int), 9)
