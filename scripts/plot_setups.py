#!/usr/bin/env python3
"""Chart winning and losing trades with the S/R decision and the Kronos forecast.

    python scripts/plot_setups.py --config config/backtest/sr_momentum_5min.yaml \
        --split m5_mine --kronos-path /path/to/Kronos --n 20

Each panel shows, in real prices:

* the candles around one round trip, with the entry marker pointing the
  direction the rule predicted;
* the support/resistance level the rule was watching when it decided, and the
  tolerance zone around it — recorded inside the strategy's bar loop, not
  redrawn afterwards;
* the close path Kronos forecast from that same decision bar.

Winners and losers are rendered by the same code onto the same axes, so the
only difference between the two galleries is the data.

Writes results/<run_id>/setups.html.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis.episodes import extract_episodes
from qtrader.config import RunConfig
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.models.kronos_confirm import (
    DEFAULT_LOOKBACK,
    Candidate,
    forecast_paths,
    load_scores,
)
from qtrader.runner import execute
from qtrader.viz.report import REPORT_CSS
from qtrader.viz.setups import setup_gallery

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><style>{css}
.note {{ max-width: 60rem; margin: 1.5rem auto; line-height: 1.55; }}
.key span {{ display: inline-block; margin-right: 1.4rem; }}
.swatch {{ display: inline-block; width: 1.6rem; height: 0; vertical-align: middle;
          margin-right: .4rem; }}
</style></head><body>
<h1>{title}</h1>
<div class="note">
<p>{lede}</p>
<p class="key">
{levels_key}{forecast_key}  <span>○ best the trade ever looked (peak)</span>
  <span>▽ position cut back (hover for how much)</span>
</p>
<p>The solid vertical line is the entry; the dotted one is the exit. A hollow
▽ marks a bar where the position was <b>reduced but not closed</b> — a scale-out
leaves a smaller position running, so without it a partial exit is
indistinguishable from an ordinary one. Each panel's second title line carries
the gate readings <b>at the decision bar</b> — momentum z, relative volume,
horizon sigma, acceptance count and the VWAP side — which is what the entry rule
actually tested, one bar before the fill.{forecast_note}</p>
</div>
{winners}
{losers}
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--kronos-path", help="clone of the Kronos repo; omit to skip forecasts")
    parser.add_argument("--scores", help="cached score frame, for the ✓/✗ in each title")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    parser.add_argument("--pred-len", type=int, default=12)
    parser.add_argument("--n", type=int, default=20, help="panels per gallery")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--context-bars", type=int, default=40)
    parser.add_argument("--out", default="setups.html", help="filename under the run directory")
    parser.add_argument("--ranges", action="store_true",
                        help="shade the market_state consolidation boxes")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    run = execute(config)
    episodes = extract_episodes(run, context_bars=args.context_bars)
    if not len(episodes):
        raise SystemExit("no completed round trips")

    winners = list(episodes.best(args.n).index)
    losers = list(episodes.worst(args.n).index)
    print(f"{config.run_id}: {len(episodes):,} episodes; "
          f"charting {len(winners)} winners and {len(losers)} losers")

    ranges = None
    if args.ranges:
        from qtrader.experiments.consolidation_gate import range_state

        # The detector's thresholds are stated in 5-minute bars. On a finer
        # decision grid the boxes would be drawn from a series the gate never
        # used, so refuse rather than draw a plausible-looking lie.
        if config.data.timeframe != "5Min":
            raise SystemExit(
                f"--ranges needs a 5-minute panel; this run is {config.data.timeframe}"
            )
        ranges = range_state(run.context.panel, list(run.context.symbols))

    scores = load_scores(args.scores) if args.scores else None
    forecasts = {}
    if args.kronos_path:
        forecasts = _forecasts(args, run, episodes, winners + losers)

    figures = {
        "winners": setup_gallery(
            episodes, winners, forecasts=forecasts, scores=scores, ranges=ranges,
            columns=args.columns,
            title=f"{args.n} best trades — {config.run_id} · {args.split}",
        ),
        "losers": setup_gallery(
            episodes, losers, forecasts=forecasts, scores=scores, ranges=ranges,
            columns=args.columns,
            title=f"{args.n} worst trades — {config.run_id} · {args.split}",
        ),
    }

    # Only a level-based strategy publishes these; the page should not claim
    # to draw lines that are not there.
    draws_levels = "watched_level" in episodes.bars.columns
    forecast_key = (
        '  <span><span class="swatch" style="border-top:2px dashed #ab47bc"></span>'
        "Kronos forecast close path</span>\n" if forecasts else ""
    )
    levels_key = (
        '  <span><span class="swatch" style="border-top:2px solid #455a64"></span>'
        "S/R level the rule watched (shaded band = break tolerance)</span>\n"
        '  <span><span class="swatch" style="border-top:2px dotted #90a4ae"></span>'
        "previous-day / opening-range levels</span>\n"
        if draws_levels else ""
    )
    ranges_key = (
        '  <span><span class="swatch" style="border-top:2px dotted #f9a825;'
        'background:rgba(249,168,37,.13);height:9px"></span>'
        "consolidation box (震荡区间) the detector was tracking</span>\n"
        if ranges else ""
    )

    path = config.output_dir() / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PAGE.format(
            title=(f"{config.strategy.name} — setups"
                   + (" and Kronos forecasts" if forecasts else "")),
            levels_key=levels_key + ranges_key,
            forecast_key=forecast_key,
            forecast_note=(
                " Everything right of the entry line in the Kronos path is"
                " prediction — the model was shown only the bars to its left."
                " \u2713/\u2717 in each title says whether the forecast agreed with"
                " the direction the strategy took." if forecasts else ""
            ),
            css=REPORT_CSS,
            lede=_lede(episodes, winners, losers, scores, _round_trip_cost(config)),
            winners=figures["winners"].to_html(full_html=False, include_plotlyjs="cdn"),
            losers=figures["losers"].to_html(full_html=False, include_plotlyjs=False),
        )
    )
    print(f"wrote {path}")


def _forecasts(args, run, episodes, episode_ids):
    """Run Kronos on the decision bar of each charted episode."""
    index = run.context.panel.index
    positions = {t: i for i, t in enumerate(index)}
    lag = run.config.execution.execution_lag_bars

    candidates = []
    for episode_id in episode_ids:
        meta = episodes.features.loc[episode_id]
        # The decision bar, not the fill: the model must be run on the same bar
        # the strategy decided on, or the picture is of a different forecast.
        at = positions[pd.Timestamp(meta["entry_time"])] - lag
        if at >= args.lookback:
            candidates.append(
                Candidate(at, meta["symbol"], 1 if meta["direction"] == "LONG" else -1)
            )

    sys.path.insert(0, args.kronos_path)
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

    print(f"loading {args.model} on {args.device} for {len(candidates)} panels ...")
    predictor = KronosPredictor(
        Kronos.from_pretrained(args.model),
        KronosTokenizer.from_pretrained(args.tokenizer),
        device=args.device,
        max_context=512,
    )
    paths = forecast_paths(
        candidates, run.context.panel, predictor,
        lookback=args.lookback, pred_len=args.pred_len, batch_size=64,
    )
    print(f"forecast {len(paths)} of {len(candidates)} panels")
    return paths


def _agreement(episodes, episode_ids, scores) -> tuple[int, int]:
    """How many of these panels Kronos pointed the same way as the strategy."""
    agreed = total = 0
    for episode_id in episode_ids:
        meta = episodes.features.loc[episode_id]
        window = episodes.window(episode_id).reset_index()
        decision = window.loc[window["offset"] == -1]
        if decision.empty:
            continue
        try:
            score = scores.at[
                pd.Timestamp(decision["timestamp"].iloc[0]), meta["symbol"]
            ]
        except KeyError:
            continue
        if not np.isfinite(score):
            continue
        total += 1
        agreed += int(np.sign(score) == (1 if meta["direction"] == "LONG" else -1))
    return agreed, total


def _weighted_gross(episodes) -> float:
    """Mean gross per round trip, weighted by the capital each one risked.

    A scale-out splits a position into a half-size slice and a remainder, and an
    unweighted mean counts the slice as a full observation. Same defect that
    inflated `search.py` before it was weighted; the headline here would be
    +1.99 bps unweighted against the number the equity curve actually feels.
    """
    features = episodes.features
    if "notional" not in features or not len(features):
        return float(features["gross_return_bps"].mean()) if len(features) else float("nan")
    weights = features["notional"].to_numpy(dtype=float)
    values = features["gross_return_bps"].to_numpy(dtype=float)
    usable = np.isfinite(weights) & (weights > 0) & np.isfinite(values)
    if not usable.any():
        return float("nan")
    return float(np.average(values[usable], weights=weights[usable]))


def _round_trip_cost(config) -> float:
    """What this run actually charges, per round trip.

    Read from the config rather than assumed: the index configs charge 1.50 bps
    against the stock universe's 3.00, and a page that states the wrong one is
    simply wrong about its own headline number.
    """
    per_side = config.costs.half_spread_bps + config.costs.slippage_bps
    return 2.0 * per_side


def _lede(episodes, winners, losers, scores, cost_bps: float) -> str:
    best = episodes.features.loc[winners, "gross_return_bps"]
    worst = episodes.features.loc[losers, "gross_return_bps"]
    line = (
        f"The {len(winners)} best trades gained {best.mean():+.0f} bps gross on average; "
        f"the {len(losers)} worst lost {worst.mean():+.0f} bps. "
        f"Across all {len(episodes):,} round trips the mean was "
        f"{_weighted_gross(episodes):+.2f} bps against a "
        f"{cost_bps:.2f} bps round-trip cost."
    )
    if scores is None:
        return line

    won, won_n = _agreement(episodes, winners, scores)
    lost, lost_n = _agreement(episodes, losers, scores)
    verdict = (
        f" If the forecast separated these two groups, the \u2713 marks would cluster in "
        f"the winners and the \u2717 marks in the losers. Kronos agreed with the "
        f"strategy on <b>{won} of {won_n}</b> winners and <b>{lost} of {lost_n}</b> "
        "losers"
    )
    if won_n and lost_n:
        gap = won / won_n - lost / lost_n
        verdict += f" \u2014 a gap of {gap:+.0%}."
    else:
        verdict += "."
    return line + verdict + (
        " These 40 are the extremes, picked by outcome, so this tally is an "
        "illustration and not the measurement: over all 15,645 candidates the "
        "rank IC is \u22120.011 (t = \u22121.35)."
    )


if __name__ == "__main__":
    main()
