#!/usr/bin/env python3
"""Download a universe's Alpaca bars into the local raw + clean layers.

    python scripts/download_data.py --config config/backtest/xsec_reversion.yaml
    python scripts/download_data.py --universe config/universe/us_liquid_22.yaml \
        --start 2026-07-28 --end 2026-08-26

Credentials are read from ALPACA_API_KEY / ALPACA_SECRET_KEY.
"""

from __future__ import annotations

import argparse
import datetime as dt

import _bootstrap  # noqa: F401  (sys.path side effect)

from qtrader.config import RunConfig
from qtrader.data.ingest import ingest_symbols
from qtrader.data.storage import BarStore
from qtrader.universe import Universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="run config YAML; supplies universe and data window")
    parser.add_argument("--universe", help="universe YAML, when no run config is given")
    parser.add_argument("--start", help="inclusive, ISO date/datetime (UTC if naive)")
    parser.add_argument("--end", help="exclusive, ISO date/datetime (UTC if naive)")
    parser.add_argument("--timeframe", default="1Min")
    parser.add_argument("--feed", default="iex", choices=["iex", "sip"])
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--keep-extended-hours",
        action="store_true",
        help="keep pre/post-market bars in the clean layer",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.config:
        config = RunConfig.from_yaml(args.config)
        universe = config.universe()
        data = config.data
        start, end = data.start_dt(), data.end_dt()
        timeframe, feed = data.timeframe, data.feed
        regular_hours_only = data.regular_hours_only
        data_root = config.data_root
    else:
        if not (args.universe and args.start and args.end):
            raise SystemExit("provide --config, or all of --universe --start --end")
        universe = Universe.from_yaml(args.universe)
        start, end = _to_utc(args.start), _to_utc(args.end)
        timeframe, feed = args.timeframe, args.feed
        regular_hours_only = not args.keep_extended_hours
        data_root = args.data_root

    symbols = universe.all_symbols
    print(f"{universe.name}: {len(symbols)} symbols, {timeframe} {feed}, {start} -> {end}")

    done = [0]

    def progress(symbol: str) -> None:
        done[0] += 1
        print(f"  [{done[0]:>3}/{len(symbols)}] {symbol}", flush=True)

    results = ingest_symbols(
        symbols,
        start,
        end,
        timeframe=timeframe,
        feed=feed,
        store=BarStore(data_root),
        regular_hours_only=regular_hours_only,
        on_progress=progress,
    )

    empty = [r.key.symbol for r in results if r.clean_rows == 0]
    total = sum(r.clean_rows for r in results)
    print(f"stored {total:,} clean bars across {len(results) - len(empty)} symbols")
    if empty:
        print(f"WARNING no data returned for: {', '.join(empty)}")


def _to_utc(value: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(value)
    return moment.replace(tzinfo=dt.timezone.utc) if moment.tzinfo is None else moment


if __name__ == "__main__":
    main()
