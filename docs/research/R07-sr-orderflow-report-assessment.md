# R07 — Assessment of the S/R + VWAP + order-flow report

**Subject:** `docs/research/deep-research-report.md`
**Question:** is this implementable here, and should it be?
**Answer:** the strong half is not implementable with this data; the implementable
half is falsified in it. **Do not implement.**

## 1. The report is good, and its own conclusion already points this way

Worth stating plainly, because the recommendation below is not a criticism of the
document. It is unusually disciplined: it opens by saying that **no peer-reviewed,
cross-market, cost-adjusted evidence exists for the combined
RBS/SBR + VWAP + absorption system**, refuses to call it "academically proven",
insists levels be generated ex-ante to avoid hindsight charting, cites the
negative literature (Neely–Weller; Jin on SHFE gold) alongside the positive, and
closes by warning against optimising the combination's Sharpe.

Its own priority ranking is:

> ex-ante S/R → **OFI/depth** → **queue imbalance** → VWAP state → time-of-day RVOL

and it is explicit that VWAP is an execution benchmark rather than alpha, and
that volume is a participation filter rather than a signal. Both match what this
project measured independently.

## 2. The strong components need data this project does not have

The report's evidence table is honest about where the weight sits. The
best-supported results — Cont–Kukanov–Stoikov on order flow imbalance across 50
NYSE names, Gould–Bonart on queue imbalance, Cartea–Donnelly–Jaimungal with a
genuine 2014 in-sample/out-of-sample split — are all **limit-order-book**
results. The report says so directly: bar data is *"不够真正检测 absorption"*,
and it recommends Nasdaq TotalView-ITCH, LOBSTER, CME MBO or Databento.

This project has IEX OHLCV bars. Measured on AAPL: **2.1% of the consolidated
tape**, median trade size 76 shares, no quotes, no book, and **no way to sign a
trade**. OFI, queue imbalance, replenishment and absorption are not
approximations away — they are unconstructible. Building a bar-derived proxy and
calling it "absorption" would be inventing the variable the evidence is about.

## 3. The implementable subset fails the gate on paper

What remains buildable is exactly what the report ranks lowest: ex-ante S/R
levels, time-of-day RVOL, and VWAP state.

| gate criterion | verdict |
| --- | --- |
| §1 mechanism | **Real but borrowed.** Osler's mechanism is conditional-order clustering — stops and takes resting at round numbers and dealer-published levels. A named counterparty exists. But the evidence is FX, 1996–98, using levels *published by six dealers*, not levels computed from price history. |
| §2 arbitraged away? | **Almost certainly.** Published in 2000/2003 and public for 25 years. Stop-cascade anticipation is a documented activity of the fastest participants. |
| §3 which side is paid | **Wrong side, and this is fatal.** The mechanism is that resting stops fire as market orders and cascade. The money accrues to whoever provides liquidity into the cascade or is positioned before it. Entering on a break-and-retest with a marketable order at the next 5-minute bar's open means arriving *after* the cascade as a taker — the same structural error as trading reversal from the taking side. |
| §4 cost | Osler reports post-crossing moves *statistically* larger than at random levels. Nothing in the literature claims a magnitude that is several times a 2.4 bps round trip, and Neely–Weller found no excess FX technical return once realistic costs were applied — a point the report itself makes. |
| §5 incremental information | **Fails outright.** S/R from price history, RVOL and VWAP are all functions of past prices and volume — the class already rejected. The one genuinely new input in the report is order flow, which is the part that cannot be built. |

Failing §3 and §5 is a rejection on paper. But the report asks for the equity
round-number effect to be re-estimated rather than transplanted, so that was
tested rather than asserted.

## 4. Two pre-registered tests, both null

Design in both cases: continuation over 6 bars after a bar crosses a level, in
the crossing direction, measured in sigma. Placebo levels are constructed
identically to the real ones, so bar size and price scale are controlled by
construction. **No free parameters.**

**Round numbers** (a bar of any size is equally likely to span a `.00` as a
`.37`, so comparing offsets is a clean control):

| level offset | crossings | continuation | t |
| --- | --- | --- | --- |
| $X.00 (round) | 170,038 | −0.0035 | −0.59 |
| $X.50 (half) | 165,747 | −0.0013 | −0.21 |
| $X.25 / .75 | 165,908 | −0.0026 | −0.42 |
| $X.37 (placebo) | 165,211 | −0.0013 | −0.21 |
| $X.13 (placebo) | 167,244 | −0.0047 | −0.78 |

Round-number continuation is **−0.0006 sigma** different from the placebo mean.
No |t| exceeds 0.8. **There is no round-number effect in this equity universe** —
which is precisely what the report said must be checked before transplanting the
FX result.

**Previous-day high/low**, with placebos at 37% and 63% of the prior day's range
— same construction, same scaling, no reason to matter:

| level | crossings | continuation | t |
| --- | --- | --- | --- |
| previous day HIGH | 15,153 | +0.0100 | +0.36 |
| previous day LOW | 13,097 | +0.0188 | +0.63 |
| placebo PDL + 0.37·range | 17,347 | −0.0145 | −0.55 |
| placebo PDL + 0.63·range | 18,358 | +0.0128 | +0.48 |

The placebo range (−0.0145 to +0.0128) **fully brackets** both real levels, and
no |t| exceeds 0.63. Previous-day extremes behave like arbitrary points on the
prior day's range.

Both ex-ante level families the report proposes are inert here. Since the levels
are the trigger for every rule in the state machine — RBS, SBR, and both
absorption sleeves all begin with "price approaches L" — a null at the trigger
makes the rest untestable rather than merely unpromising.

## 5. Conclusion

**Do not implement.** Not because the report is weak — it is the most careful
document in this repository — but because it splits cleanly into a half that
needs data this project does not have and a half that this project's data
falsifies.

That conclusion agrees with the report's own. It says no proven combined system
exists, ranks order flow above the price-derived components, and asks for the
round-number effect to be re-estimated for equities. It has now been
re-estimated, and it is absent.

### What would change the answer

The report's strong half becomes implementable with a limit-order-book feed —
Databento MBO, LOBSTER, Nasdaq TotalView-ITCH, or NYSE TAQ, all of which it
lists. With signed trades and book depth, OFI and queue imbalance are
constructible, and they are the components carrying the actual evidence.

That is a **data** decision, not a research one, and it should be taken on the
same terms as the gate: what is the expected edge, which side of the trade is
paid, and can this project's execution be on that side. Note that §3 does not
resolve itself with better data — Cartea et al. improved *execution* with the
signal, and Gould–Bonart predict the next mid-price move, which is a quoting
horizon. Buying tick data would answer whether the signal exists; it would not
by itself make a slow taker the one who gets paid for it.
