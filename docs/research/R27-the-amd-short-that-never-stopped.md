# R27 — Why the AMD short never stopped, and why no long was taken

**Date:** 2026-09-08
**Case:** AMD 2026-06-29, SHORT entered 512.69, exited 537.99, **−490 bps**,
70 bars held, in the wide-exit gallery from R26.

**Both questions have exact answers, and the second one qualifies R25/R26: the
wide exit's positive return is bought with a seven-fold increase in
catastrophic single trades.**

## 1. Why no stop

The decision bar is 10:00, right after a gap-down open, and the volatility
estimate there is enormous:

| | |
| --- | --- |
| σ_H at the decision bar | **314 bps** |
| entry | 512.69 |
| initial stop = entry + `stop_sigmas` 2.0 × σ_H | **544.91 — 32.2 points away** |
| trail width = 3.5 × σ_H | 56.4 points |
| session high | **542.08** |

**The stop sat 2.8 points above the session's high.** It was never touched, the
ratchet never armed, and the position ran to `flat_time`.

Nothing malfunctioned — this is the geometry doing exactly what it was
configured to do. The configuration is the problem: **R25 deleted the 2-ATR cap
that exists to stop precisely this.** With `initial_stop_atr: 2.0` restored,
the same trade exits at **523.0 for −198 bps** instead of −490.

The cap's purpose was never to improve the average. It was to stop a single
inflated σ reading from writing a blank cheque, and on a gap-down open σ_H is
314 bps when the same name reads 88–106 bps four hours later.

## 2. Why no long during the rally

`watched_level` is **NaN on every bar** from the entry to the close. No upside
break was ever registered, so the momentum gate never got a say. The cause is
explicit in `_track_break`:

```python
watching = (
    np.ones_like(position, dtype=bool)
    if (self.exit_on_opposite_signal or self.allow_add_back)
    else (position == FLAT)
)
```

**Breaks register only while the symbol is flat.** From the moment the short
opened at 10:05, AMD stopped forming setups entirely: a 29-point rally was
structurally invisible. The long was not evaluated and rejected — it was never
evaluated.

There is a switch for it, and **it does not help here.** With
`exit_on_opposite_signal: True` the AMD trade is still −490 bps with the same
exit, because `momentum_z` stays negative until 13:00 and only clears the +0.25
threshold at 13:15, by which time price is already 537. The opposite signal
arrives after the move it was supposed to catch.

| bar | 10:30 | 11:15 | 12:00 | 12:45 | 13:00 | 13:15 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| close | 510 | 520 | 528 | 531 | 533 | 537 |
| `momentum_z` | −1.20 | −0.57 | −0.23 | −0.11 | **+0.11** | **+0.62** |

The statistic is a lagging average of the move it is supposed to lead.

## 3. The finding that matters

Applying each fix to the whole `m5_test` window:

| arm | AMD 06-29 | n | gross | net | return | **worst trade** | **trades < −200 bps** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **wide (R25/R26)** | −490 | 1058 | +3.98 | +0.98 | **+1.47%** | **−711** | **56** |
| wide + 2-ATR cap | −198 | 1497 | +0.86 | −2.14 | −4.40% | −273 | 10 |
| wide + exit_on_opposite | −490 | 1086 | +4.01 | +1.01 | +1.53% | −711 | 53 |
| original tight | −198 | 1570 | +2.38 | −0.62 | −1.36% | −273 | 8 |

**Removing the ATR cap is the whole of R25's result, and it is a
short-volatility trade.** It turns 8 trades worse than −200 bps into **56**, and
the worst single trade from −273 to **−711 bps**. At `max_weight: 0.15` a
−711 bps trade is about **−1.07% of equity in one position**.

Restoring the cap on top of the wide trail is *worse* than the original tight
config (−4.40% vs −1.36%), so the two cannot be separated: the +1.47% and the
fat left tail are the same mechanism.

## 4. How R25 and R26 should now be read

R25 §1's measurement stands — losers do reach +35 bps MFE before ending at −52,
and the exit was cutting trades that were working. R26's +1.64% / +1.47% also
stand as arithmetic.

What was missing is the shape of the distribution that produces them. The wide
exit does not find better trades; it stops taking small losses and starts taking
occasional very large ones, and over these two windows the trade happened to pay.
A result whose worst observation is −711 bps and whose sample contains 56 such
tail events is not a 1.47% edge with noise around it — it is a short-volatility
position whose payoff depends on how many gap days the window contained.

That is the same failure mode as R24, arrived at from the opposite direction:
there the mechanism cut the right tail, here it grows the left one.

## 5. Next

1. **Reinstate a cap on the initial stop, but not the ATR one.** The defect is a
   single inflated σ_H reading, so cap σ_H itself — e.g. against its own
   trailing session median — rather than capping the stop with a second
   volatility measure that has its own failure modes. Then re-run R25 §3.
2. Report `worst trade` and `count < −200 bps` in every future arm table.
   Neither R25 nor R26 would have been written the same way with those columns
   present.
3. `exit_on_opposite_signal` is not the fix for §2 and should not be enabled on
   the strength of this case.
