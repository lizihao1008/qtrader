"""A K-line image of the decision bar's window, as auxiliary model input.

matplotlib rather than the project's plotly charts: plotly needs `kaleido` to
reach a PNG, which is not installed, and this runs once per candidate. Measured
at 0.085 s and 19 KB per image, which is negligible beside ~8 s of inference.

The image shows **only** the snapshot's bars, so it cannot disagree with the
text about what was visible. The decision bar is marked; nothing to its right is
drawn, because nothing to its right had happened.
"""

from __future__ import annotations

import io

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

UP, DOWN = "#26a69a", "#ef5350"


def render(snapshot, *, width: float = 6.4, height: float = 3.4, dpi: int = 110) -> bytes:
    """PNG bytes of the candidate's window, or empty if there is nothing to draw."""
    bars = snapshot.bars.dropna(subset=["open", "high", "low", "close"])
    if bars.empty:
        return b""

    figure, (price, volume) = plt.subplots(
        2, 1, figsize=(width, height), dpi=dpi, sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.0], "hspace": 0.06},
    )
    opens = bars["open"].to_numpy()
    closes = bars["close"].to_numpy()
    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    colours = [UP if c >= o else DOWN for o, c in zip(opens, closes)]

    for i, colour in enumerate(colours):
        price.plot([i, i], [lows[i], highs[i]], color=colour, lw=0.8, solid_capstyle="butt")
        price.plot([i, i], [opens[i], closes[i]], color=colour, lw=3.0, solid_capstyle="butt")

    last = len(bars) - 1
    price.axvline(last, color="#455a64", lw=1.1)
    price.annotate("decision", xy=(last, highs[last]), xytext=(-4, 6),
                   textcoords="offset points", ha="right", fontsize=8, color="#455a64")

    side = "LONG" if snapshot.direction > 0 else "SHORT"
    clock = snapshot.session.get("clock", "")
    price.set_title(f"{snapshot.symbol} — quant proposes {side} at {clock}", fontsize=9.5)
    price.grid(alpha=0.25, lw=0.5)
    price.set_ylabel("price", fontsize=8)
    price.tick_params(labelsize=7)

    volume.bar(np.arange(len(bars)), bars["volume"].fillna(0.0).to_numpy(),
               color=colours, width=0.7)
    volume.grid(alpha=0.25, lw=0.5)
    volume.set_ylabel("volume", fontsize=8)
    volume.set_xlabel("bars (rightmost is the decision bar)", fontsize=8)
    volume.tick_params(labelsize=7)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(figure)
    return buffer.getvalue()
