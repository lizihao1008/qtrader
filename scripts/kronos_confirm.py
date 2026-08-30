#!/usr/bin/env python3
"""Score a strategy's entry candidates with Kronos and cache the result.

    python scripts/kronos_confirm.py --config config/backtest/sr_momentum_5min.yaml \
        --split m5_mine --kronos-path /path/to/Kronos

Runs the model only where the strategy proposed an entry, so the same candidate
set can be backtested with and without the filter and the difference attributed.
The context window ends at the candidate bar inclusive; the forecast begins
after it.

Writes results/<run_id>/kronos_scores.parquet.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd
from _bootstrap import parse_assignments  # noqa: F401  (also sets sys.path)

from qtrader.config import RunConfig
from qtrader.data.sessions import session_date
from qtrader.experiments.splits import apply_split, load_splits
from qtrader.features.relative import bar_log_returns
from qtrader.features.seasonality import seasonal_volatility
from qtrader.models.kronos_confirm import (
    DEFAULT_BATCH,
    DEFAULT_LOOKBACK,
    collect_candidates,
    save_scores,
    score_candidates,
)
from qtrader.runner import build_context
from qtrader.strategies import build_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--split")
    parser.add_argument("--splits-file", default="config/splits.yaml")
    parser.add_argument("--kronos-path", required=True, help="clone of the Kronos repo")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    parser.add_argument("--pred-len", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()

    config = RunConfig.from_yaml(args.config)
    if args.split:
        config = apply_split(config, load_splits(args.splits_file)[args.split])
    config = config.with_overrides(parse_assignments(args.set))

    context = build_context(config)
    strategy = build_strategy(config.strategy.name, config.strategy.params)
    signals = strategy.generate(context)

    candidates = collect_candidates(signals.indicators, context.index, args.lookback)
    print(f"{config.run_id}: {len(candidates):,} entry candidates to score")
    if not candidates:
        raise SystemExit("no candidates")

    sys.path.insert(0, args.kronos_path)
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

    print(f"loading {args.model} on {args.device} ...")
    predictor = KronosPredictor(
        Kronos.from_pretrained(args.model),
        KronosTokenizer.from_pretrained(args.tokenizer),
        device=args.device,
        max_context=512,
    )

    close = context.panel.close[list(context.symbols)]
    day = session_date(close.index)
    bar_of_session = pd.Series(day.to_numpy(), index=close.index).groupby(
        day.to_numpy()
    ).cumcount()
    volatility = seasonal_volatility(
        bar_log_returns(close, within_session=True),
        session=day, bar_of_session=bar_of_session,
        window=config.strategy.params.get("vol_window", 24), min_periods=12,
    )

    started = time.time()

    def progress(done: int, total: int) -> None:
        rate = done / max(time.time() - started, 1e-9)
        print(f"  {done:>7,}/{total:,}  {rate:5.1f} windows/s  "
              f"eta {(total - done) / max(rate, 1e-9) / 60:5.1f} min", flush=True)

    scores = score_candidates(
        candidates, context.panel, volatility, predictor,
        lookback=args.lookback, pred_len=args.pred_len,
        batch_size=args.batch_size, sample_count=args.sample_count, progress=progress,
    )

    path = save_scores(scores, config.output_dir() / "kronos_scores.parquet")
    scored = scores.notna().sum().sum()
    flat = scores.stack().dropna()
    print(f"\nscored {scored:,} of {len(candidates):,} candidates in "
          f"{(time.time()-started)/60:.1f} min -> {path}")
    print(f"score distribution: mean {flat.mean():+.3f}, sd {flat.std():.3f}, "
          f"share positive {(flat > 0).mean():.1%}")


if __name__ == "__main__":
    main()
