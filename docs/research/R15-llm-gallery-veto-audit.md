# R15 — LLM veto audit on 20 best and 20 worst charts

**Date:** 2026-09-02  
**Strategy:** canonical `sr_momentum_5min`, `m5_mine`  
**Model:** local `Qwen3.6:27b-mlx`, temperature 0, confidence threshold 0.6

## 1. Question and design

Can the existing LLM validation layer reject the visually obvious failures in
R14's gallery without rejecting its successes?

The test selects exactly the same 20 largest and 20 smallest completed trades
by gross return as `setups_atr_acceptance.html`. Each episode is mapped back to
the strategy's original position-run candidate. The model receives only:

- 60 five-minute bars ending at the decision bar;
- completed six-bar (30-minute) aggregates of the same backward-looking window;
- the strategy indicators and session state available at that bar;
- a chart that ends at the same decision bar.

It never sees entry outcome, MFE/MAE, exit, profit or the winner/loser label. It
may keep or veto only; it cannot reverse or resize the proposal. An unusable
reply is an abstention and the fail-open policy keeps that trade.

This is an **outcome-selected visual audit of extremes**, not an out-of-sample
accuracy estimate. It answers whether the model can recognise the most obvious
historical tails, not how it would perform across all ordinary candidates.

## 2. Output-contract fix

The installed MLX Qwen variants ignored Ollama's `format=<JSON schema>` unless
the prompt itself explicitly demanded JSON-only output. Four initial calls hit
the 45-second timeout, and two probes returned Markdown prose. None were counted
as decisions.

The prompt now includes the exact JSON object contract in addition to the API
schema. That made responses machine-validated again. The formal run used a new
journal and a 120-second transport timeout; no decision expired against the
five-minute live latency budget.

## 3. Result

| selected group | keep | veto | abstain | veto rate |
| --- | ---: | ---: | ---: | ---: |
| 20 best trades | 18 | **0** | 2 | **0.0% false veto** |
| 20 worst trades | 16 | **1** | 3 | **5.0% loser veto** |

- veto-rate separation: **+5.0 percentage points**;
- balanced accuracy under fail-open abstention: **52.5%**;
- veto precision: 1/1, but this is one observation, not evidence of precision;
- the sole veto was `NVDA_20240530T1345_L`, −171 bps;
- the 20 worst trades lost −4,040.5 bps in total, so the veto removed only
  **4.2%** of their summed gross loss;
- among usable replies, mean confidence in the proposed side was **0.783 for
  winners and 0.815 for losers**. The model was slightly more confident in the
  bad trades, the opposite of useful ranking.

Five of 40 responses were truncated/malformed JSON at the 220-token generation
budget and therefore abstained (2 winners, 3 losers). Under the configured
fail-open policy they remain trades. Even the most favourable assumption that
all three loser abstentions would have become vetoes caps observed loser
rejection at 20%; the actual reproducible action rate is 5%.

## 4. Interpretation

The LLM preserved successful extreme trends, but mostly because it preserved
almost everything. It also endorsed 16 of 17 parseable worst trades, often with
high confidence. A single correct veto cannot distinguish this from a nearly
always-keep classifier; with one veto split 1 loser/0 winners, the exact
one-sided assignment probability is 0.5.

The failure mode is understandable from the prompt. The quant candidate already
requires momentum, relative volume, VWAP side and a structural break. The LLM
then reads the same contemporaneous evidence and restates the setup thesis. The
information needed to know that continuation will fail is not present in these
bars, and the model has no independent predictive signal.

**Decision:** do not enable this LLM as a production veto on the evidence from
these 40 charts. It adds **42.6 seconds median** decision latency, abstains on
12.5%, and rejects only 5% of the selected failures. Before any full 4,037-trade
run, require a blinded candidate sample and an LLM score that separates outcomes
without labels. The full run would take roughly 45 hours at the observed median
latency and is not justified by this screen.

## 5. Artifacts and reproducibility

Command:

```bash
python scripts/test_llm_gallery.py \
  --config config/backtest/sr_momentum_5min.yaml --split m5_mine \
  --model Qwen3.6:27b-mlx --n 20 --min-confidence 0.6 \
  --timeout 120 --num-predict 220 \
  --journal llm_gallery_decisions_qwen36_json.jsonl
```

Artifacts:

- `results/sr_momentum_5min__m5_mine/setups_atr_acceptance_llm.html` — the same
  40 charts with KEEP/VETO/ABSTAIN and confidence in every title;
- `results/sr_momentum_5min__m5_mine/llm_gallery_summary.json` — group metrics
  and per-episode outcomes;
- `results/sr_momentum_5min__m5_mine/llm_gallery_decisions_qwen36_json.jsonl` —
  causal snapshot, prompt, raw reply, parsed verdict, action and latency.

`m5_test` was not read or run.
