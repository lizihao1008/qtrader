# Running the win/loss galleries yourself

Every strategy in this repository can be charted the same way: 20 best and 20
worst round trips, side by side, drawn by identical code onto identical axes so
that the only difference between the two panels is the data.

Everything below assumes the `quant` conda environment:

```bash
PY=/opt/miniconda3/envs/quant/bin/python
```

---

## The one command

```bash
python scripts/plot_setups.py --config <config> --split <split> --n 20
```

It runs the backtest, extracts every round trip, picks the 20 best and 20 worst
by gross return, and writes `results/<run_id>/setups.html`. Open that file in a
browser.

`--n 20` is the number of panels *per gallery*, so 20 gives you 40 charts.

### Adding the Kronos forecast

```bash
python scripts/plot_setups.py --config <config> --split <split> --n 20 \
    --kronos-path /path/to/Kronos
```

`--kronos-path` is a clone of <https://github.com/shiyu-coder/Kronos>. The model
downloads itself on first use (~100 MB) and runs on the Mac GPU. It only scores
the 40 charted panels, so this adds about a minute.

Add `--scores <parquet>` as well to get the ✓/✗ agreement mark in each panel
title — that needs a full score file, see "Regenerating Kronos scores" below.

---

## Per strategy

### 1. `sr_momentum` — break of an ex-ante level, confirmed by momentum

```bash
python scripts/plot_setups.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine --n 20 --kronos-path /path/to/Kronos --scores results/sr_momentum_5min__m5_mine__use_previous_dayFalse_require_retestFalse_sizingequal/kronos_scores.parquet
```

Output: `results/sr_momentum_5min__m5_mine/setups.html`

The canonical experiment config uses the R13 ten-minute observation gate plus
R14's one-bar break acceptance and 2-ATR initial stop. Its current audited
gallery (20 best + 20 worst, no Kronos overlay) is:

```bash
python scripts/plot_setups.py --config config/backtest/sr_momentum_5min.yaml \
  --split m5_mine --n 20 --out setups_atr_acceptance.html
```

Output:
`results/sr_momentum_5min__m5_mine/setups_atr_acceptance.html`.
R14 records that this is a visual/risk-control experiment, not a performance
improvement: the combined configuration lost −12.80% on `m5_mine`.

To ask the local LLM to judge exactly those 20 best and 20 worst episodes and
annotate each panel with KEEP/VETO/ABSTAIN:

```bash
python scripts/test_llm_gallery.py \
  --config config/backtest/sr_momentum_5min.yaml --split m5_mine \
  --model Qwen3.6:27b-mlx --n 20 --min-confidence 0.6 \
  --timeout 120 --num-predict 220 \
  --journal llm_gallery_decisions_qwen36_json.jsonl
```

Output:
`results/sr_momentum_5min__m5_mine/setups_atr_acceptance_llm.html` plus a JSON
summary and append-only verdict journal. R15 records the measured 5% loser veto
rate and 0% winner false-veto rate; this extreme-case audit did not justify a
full LLM backtest.

This is the only strategy that publishes levels, so its panels carry the S/R
line, the shaded break-tolerance band, and the previous-day / opening-range
lines. The others degrade to candles, markers, peak and forecast.

### 2. `trend_ratchet` — drift state entry, monotone volatility exit

```bash
python scripts/plot_setups.py --config config/backtest/trend_ratchet_5min.yaml --split m5_mine --n 20 --kronos-path /path/to/Kronos
```

Output: `results/trend_ratchet_5min__m5_mine/setups.html`

### 3. `cross_sectional_residual` — residual z-score, top/bottom-k

```bash
python scripts/plot_setups.py --config config/backtest/xsec_reversal_5min.yaml --split m5_mine --n 20 --kronos-path /path/to/Kronos
```

Output: `results/xsec_reversal_5min__m5_mine/setups.html`

### 4. `ma_cross` — single-name moving-average crossover

```bash
python scripts/plot_setups.py --config config/backtest/ma_cross_pltr.yaml --split burned --n 20 --kronos-path /path/to/Kronos
```

Output: `results/ma_cross_pltr__burned/setups.html`

This one is 1-minute PLTR only, and its data window sits inside `burned` — a
contaminated split. It is a demo, not a research result.

---

## Options worth knowing

| flag | what it does |
| --- | --- |
| `--n 20` | panels per gallery (20 best + 20 worst) |
| `--columns 4` | grid width |
| `--context-bars 40` | bars of setup drawn before each entry |
| `--out name.html` | filename under the run directory — **use this** when charting variants, or they overwrite each other |
| `--set NAME=VALUE` | override any strategy parameter, repeatable |
| `--pred-len 12` | how many bars ahead Kronos forecasts |

### Charting a variant without editing the config

```bash
python scripts/plot_setups.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine --n 20 --set sizing=risk --set require_retest=true --out setups_riskrisk.html
```

`--set` values go into the run id, so variants land in their own directory.

---

## Reading a panel

* **solid vertical line** — the entry. **dotted vertical line** — the exit.
* **▲ / ▼** — the direction the rule predicted.
* **✗** — where the position was closed.
* **○** — the best the trade ever looked between entry and exit. On a winner it
  sits next to the exit, meaning the exit released near the peak. On a loser it
  sits next to the *entry*, meaning the trade never went the right way at all.
* **solid dark horizontal line** — the level the rule was watching, with its
  break tolerance shaded (`sr_momentum` only).
* **purple dashed** — Kronos's forecast close path. Everything right of the
  entry line is prediction; the model saw only the bars to its left.
* **✓ / ✗ in the title** — whether the forecast agreed with the direction taken.

Prices are real, not normalised, and shorts are **not** flipped — a support
level does not survive being rescaled per panel. For the opposite view
(normalised to basis points from entry, shorts flipped so up is always profit)
use the older `episode_gallery` through `scripts/analyze_episodes.py`.

---

## Related commands

### One backtest with the full attribution

```bash
python scripts/search.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine --label my-test --hypothesis "what I expect this to do"
```

Prints expectancy net of costs, Sharpe, max drawdown, trades, hit rate, profit
factor and the long/short split, and appends the run to
`results/search/ledger.jsonl`. **Every run is recorded**, including the failures,
because the number of trials against a window is the multiple-testing burden for
anything later found on it.

### Check the fills are realistic

```bash
python scripts/audit_execution.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine
```

Five checks: fills at the next bar's open, prices inside `[low, high]`, spread
always paid adversely, no fills on bars with no print, and — the important one —
that perturbing all future bars leaves every earlier fill identical.

### Does a confirmation model know anything?

```bash
python scripts/analyze_confirmation.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine --scores <scores.parquet>
```

Rank IC of the score against the realised outcome of the candidates, plus a
threshold sweep. Run this **before** writing a backtest around any filter: a
filter with no rank IC here cannot help one.

### Regenerating Kronos scores

Needed after **any** change to the entry logic, because the scores are keyed to
candidate bars:

```bash
python scripts/kronos_confirm.py --config config/backtest/sr_momentum_5min.yaml --split m5_mine --kronos-path /path/to/Kronos
```

Writes `results/<run_id>/kronos_scores.parquet`. Expect ~26 windows/second on an
M3 Pro; the current `sr_momentum` config has ~50,000 candidates, so about half
an hour.

---

## Splits

Defined in `config/splits.yaml`. Use `m5_mine` for anything exploratory.

| split | window | use |
| --- | --- | --- |
| `m5_mine` | 2024-01-02 → 2025-04-01 | hypothesis generation. **33 trials recorded** — Bonferroni puts the bar near \|t\| = 3.3 |
| `m5_validate` | 2025-04-01 → 2026-01-01 | spent by the one R12 confirmation; do not tune against it |
| `m5_test` | 2026-01-01 → 2026-08-27 | final holdout, partially contaminated |
| `burned` | 2026-07-28 → 2026-08-26 | parameters were chosen on it; not a test set |

---

## One caveat on every number these produce

The cost model charges a flat 1.0 bps half-spread at 09:35 and at 15:20 alike.
Measured on 1-minute bars, the Roll half-spread is **4.91 bps in the first five
minutes against 0.34 bps mid-day** — a factor of 14. Strategies whose turnover
concentrates at the open have inflated backtests here. See R10 §5.
