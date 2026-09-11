# R26 — Fitting the exit width per symbol

**Date:** 2026-09-08
**Instruction:** execute R25 §6 step 1 and report whether the result is
positive.

> **Read with R27.** The result below is bought by deleting the 2-ATR
> initial-stop cap, and that trade turns 8 trades worse than −200 bps
> into 56, with the worst single trade going from −273 to −711 bps. It
> is a short-volatility payoff, not a 1.5% edge with noise around it.


**Answer: yes, positive — but from the single global width, not from the
per-symbol fit. The per-symbol optimum is noise: its rank correlation across
two windows is +0.000. The one-parameter version returns +1.64% and +1.47% on
the two later windows at the full 3.00 bps cost, and −1.24% on the training
window.**

## 1. What was built

`SRMomentumStrategy.stop_sigmas` and `.trail_sigmas` now accept either a float
(unchanged behaviour, scalar arithmetic) or a `{symbol: width}` mapping that
must carry its own `"default"` — so a symbol the fit never saw cannot silently
inherit the class default. `trail >= stop` is checked symbol by symbol rather
than by extremes.

## 2. The fit

`trail_sigmas` swept over `[2.0, 2.5, 3.0, 3.5, 4.5, 6.0]` per symbol on
`m5_mine` **only**, with `stop_sigmas: 2.0` and no ATR cap. Best-by-gross:

| | AAPL | AMD | AMZN | BAC | GOOGL | JPM | META | MSFT | MU | NVDA | TSLA | XOM |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fitted on `m5_mine` | 3.5 | 2.5 | 2.0 | 2.0 | 2.0 | 3.5 | 3.5 | 2.0 | 3.0 | 4.5 | 2.5 | 2.0 |
| best on `m5_validate` | 6.0 | 2.0 | 4.5 | 2.0 | 2.5 | 2.5 | 2.5 | 3.5 | 6.0 | 2.5 | 3.5 | 4.5 |

**The two windows agree on 1 of 12 symbols. Chance agreement on a six-point
grid is 2.** Mean correlation between each symbol's two curves: **−0.202**.
Rank correlation of the fitted optimum across windows: **+0.000**.

The per-symbol optimum is not a property of the symbol. It is a property of the
window, which is to say it is noise. Several symbols make this visible directly:
TSLA's curve reads −1.23, +1.52, −0.61, −2.34, −2.93 across the grid — an
argmax picked off a single spike — while NVDA is +26 to +29 flat, so its
"best 4.5" means nothing.

## 3. Out of sample

Widths frozen after the fit; both later windows scored once.

| split | arm | trades | hit | gross | net | return |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `m5_mine` | original tight | 3153 | 0.368 | +1.11 | −1.89 | −7.95% |
| | global wide (trail 3.5) | 2029 | 0.492 | +2.55 | −0.45 | −1.24% |
| | per-symbol fitted | 2098 | 0.486 | +2.97 | −0.03 | −0.10% *(in-sample)* |
| `m5_validate` | original tight | 2022 | 0.354 | +1.56 | −1.44 | −4.23% |
| | **global wide** | 1335 | 0.473 | +3.79 | **+0.79** | **+1.64%** |
| | per-symbol fitted | 1394 | 0.463 | +3.25 | +0.25 | +0.54% |
| `m5_test` | original tight | 1570 | 0.387 | +2.38 | −0.62 | −1.36% |
| | **global wide** | 1058 | 0.494 | +3.98 | **+0.98** | **+1.47%** |
| | per-symbol fitted | 1093 | 0.496 | +5.07 | +2.07 | +3.22% |

The fit beats the global width on `m5_test` (+3.22% vs +1.47%) and loses on
`m5_validate` (+0.54% vs +1.64%). Given §2 that is a coin flip, not an
improvement. **Twelve fitted parameters do not beat one.**

## 4. So: is there positive return?

**Yes, on two of three windows, from a one-parameter change** — widen the
ratchet to `trail_sigmas: 3.5`, widen the initial stop to `stop_sigmas: 2.0`,
and delete the 2-ATR cap:

| | return | net bps/trade |
| --- | ---: | ---: |
| `m5_mine` (2024-01 → 2025-04) | −1.24% | −0.45 |
| `m5_validate` (2025-04 → 2026-01) | **+1.64%** | **+0.79** |
| `m5_test` (2026-01 → 2026-08) | **+1.47%** | **+0.98** |

Positive net of the full 3.00 bps assumption, on the two windows that matter
most. That is the first time in this project.

**Four things that must be said with it:**

1. It is still negative on `m5_mine`, the largest window.
2. R25 §4: the same three parameters fail on 2 of 6 out-of-universe cells
   (SPY+QQQ `m5_test` gross +0.74 → −1.24; us_liquid_22 `m5_validate`
   +0.21 → −1.53).
3. The magnitude is ~**+2.2%/year**. R20 measured buy-and-hold on these indices
   at +24% over the trailing year, and Treasury bills pay more than 2.2%. This
   is positive, not useful.
4. `m5_test` now carries 8 trials and `m5_validate` 7. Any claim from them
   carries that burden.

## 5. Next

1. **Stop fitting exit width per symbol.** §2 settles it.
2. The mechanism from R25 §1 — losers reach +35 bps before ending at −52 —
   remains the only thing in this project with a pre-registered prediction that
   was then confirmed. Whatever comes next should attack *that*, at the level of
   the exit rule's form rather than its parameters.
3. Do not resume entry filtering (six attempts, all neutral) and do not add a
   hit-rate-raising exit (R25 §2, negative in every window).
