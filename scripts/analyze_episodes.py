#!/usr/bin/env python3
"""Diagnose which of a strategy's trades worked, and what the setup looked like.

    python scripts/analyze_episodes.py --config config/backtest/xsec_reversion.yaml --split mine

Works on any strategy without configuration: every run is screened against the
universal market features, plus whatever the strategy exposes through
``Strategy.setup_features``. Writes results/<run_id>/episodes/ (the K-line
windows and per-trade features as parquet) and an HTML report.

Without --split the config's own date range is used. Generate hypotheses on
`mine`; confirm them on `validate`, once.
"""

from __future__ import annotations

import argparse

from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.analysis import diagnose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--split",
        help="named window from config/splits.yaml; omit to use the config's own range",
    )
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--context-bars", type=int, default=60)
    parser.add_argument("--gallery", type=int, default=6, help="episodes per side in the report")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override one strategy parameter, e.g. --set max_abs_zscore=none",
    )
    args = parser.parse_args()

    diagnosis = diagnose(
        args.config,
        split=args.split,
        splits_file=args.splits_file,
        overrides=parse_assignments(args.set),
        context_bars=args.context_bars,
    )
    if not len(diagnosis.episodes):
        raise SystemExit("no completed round trips in this window")

    if diagnosis.split is not None and diagnosis.split.name == "burned":
        print("NOTE this window chose the parameters; it cannot test them.\n")

    print(diagnosis.summary())
    print("\nConditions vs gross return:")
    print(diagnosis.conditions.round(3).to_string(index=False))
    print("\nWinners vs losers:")
    print(diagnosis.contrast.round(3).to_string(index=False))

    directory = diagnosis.save()
    report = diagnosis.write_report(gallery_size=args.gallery)
    print(f"\nsaved {directory} · report {report}")


if __name__ == "__main__":
    main()
