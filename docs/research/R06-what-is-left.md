# R06 — What is left with this data, and what would actually break through

Written against [the gate](GATE.md). The purpose is to reject, not to propose.

## Part 1: is there really nothing left in the current data?

The data is IEX 1- and 5-minute bars, ~130 US large caps and sector ETFs,
2024-01 to 2026-08. Each bar carries open/high/low/close, volume, **trade_count**
and **vwap** — the last two never used. Taking each remaining possibility in
turn:

### Order flow from trade_count — rejected, and the reason is structural

`volume / trade_count` gives average trade size, which is a genuine new input
rather than another transform of past prices. The mechanism would be
institutional order splitting: a large order worked over hours leaves a
persistent footprint, and following it is trading alongside someone who is still
buying.

It fails on two counts, either fatal:

* **The flow cannot be signed.** The mechanism needs to know *which way* the
  flow goes. Bar data gives volume and a trade count, not buy/sell
  classification. Without signs there is no order-flow imbalance, only activity.
* **The sample is 2% of the tape and biased.** Measured on AAPL: median IEX
  volume is 13,268 shares per 5-minute bar against roughly 640,000 for the
  consolidated tape — **2.1% coverage** — at a median trade size of 76 shares.
  That is not a sample of institutional flow; it is a sliver skewed toward small
  orders. Inferring institutional footprints from it is inference from the wrong
  population.

### VWAP — rejected under §5

Provider-computed from the same bars. A transform of prices already tested.

### ETF versus constituent dislocation — rejected under §2 and §3

A real mechanism (authorised participants arbitrage NAV gaps) and the most
heavily competed one in existence, run at microsecond latency by firms with
direct feeds. With 2% tape coverage the dislocation cannot even be measured
correctly, and capturing it requires being the fastest taker or a quoting AP.

### Sector-ETF lead-lag — rejected under §2

Same objection. Any lead-lag between a liquid ETF and a mega-cap constituent is
closed well inside one 5-minute bar.

### Overnight versus intraday decomposition — the only survivor, and it is weak

The one candidate that is not obviously dead. Close-to-open and open-to-close
returns have documented different properties, and unlike everything above the
mechanism puts this project on the **receiving** side of a payment: holding
overnight gap risk is a risk others are willing to pay to avoid.

Against the gate:

| criterion | assessment |
| --- | --- |
| mechanism | real — compensation for bearing overnight gap risk |
| arbitraged away? | **partly.** Documented since Lou–Polk–Skouras (2019) and widely traded |
| which side pays | this project would be **paid** — it bears the risk |
| cost vs edge | one round trip per day against a full overnight move: the best cost ratio available here |
| incremental information | **weak** — still a partition of past prices, not a new input |
| failure regime | gap-risk events; it is short volatility by construction |

**Verdict: marginal.** It survives §1–§4 and fails §5. It is a risk premium
rather than an alpha — closer to a beta harvest with a known name — and its
capacity has been public for years. Worth one pre-registered test if anything
here is, but nobody should expect it to be an edge.

**So: yes, with this data there is essentially nothing worth building.** That is
a statement about the data and the venue, not a failure of imagination.

## Part 2: what would actually break through

"Advanced" is usually taken to mean a more sophisticated model. On this evidence
that is precisely the wrong direction. The rejections above are not about
technique; they are about being a slow taker with 2% of the tape. A deeper model
on the same inputs inherits every one of those constraints.

### Rejected outright, no test warranted

| family | why it fails here |
| --- | --- |
| **ML / deep learning on price bars** | Same inputs already shown to carry a wrong-signed or spread-sized signal, plus far more parameters. This is the failure mode the gate exists to prevent — it optimises for finding a curve. Rejected under §5. |
| **Statistical arbitrage, pairs, cointegration** | Same taker problem as reversal: the spread convergence is the market maker's compensation. Extremely crowded. |
| **Microstructure / order-flow imbalance** | Needs signed tick data and latency. Both absent. |
| **HFT, latency arbitrage, queue position** | Not a strategy question; an infrastructure one this project cannot enter. |
| **Alternative data (satellite, card, web)** | The cost *is* the barrier, which is what makes it work for those who pay it. Not available. |

### Worth the cost of testing — in order of expected value here

**1. Daily-horizon cross-sectional equity.** The single highest-value change,
because it fixes the binding constraint rather than working around it. Cost per
round trip stays ~2.4 bps while holding goes from one hour to weeks, so the
cost-to-edge ratio improves by one to two orders of magnitude. Latency stops
mattering, so the competitors with better infrastructure lose their advantage.
Alpaca provides daily bars for thousands of names going back years, which is
~50x the breadth. Mechanisms here are documented and — critically —
capacity-constrained, which is a real answer to §2: some of these are not
arbitraged away because doing so at size moves the price.

The measurement framework built in this project transfers unchanged.

**2. Futures trend and carry.** Worth naming explicitly because this project's
finding that "trend does not work" is **venue-specific and does not transfer**.
Trend following has survived decades in futures for reasons equities intraday
lacks: hedgers systematically pay to transfer risk, position sizes are
constrained by margin rather than by information, and transaction costs are tiny
relative to volatility. The mechanism names a counterparty (the commercial
hedger) who is not trying to make money on the trade. Data and execution are
both obtainable.

**3. Event-driven with a data barrier.** Earnings announcements, index
additions and deletions, secondary offerings. The mechanism is the strongest
available anywhere: a participant who *must* trade on a schedule regardless of
price. Requires event data, which is the barrier and therefore also the reason
the effect persists. Works at horizons where execution disadvantage is small.

**4. Options-implied volatility.** The one thing this project measured as
strongly predictable was volatility — rank IC **+0.67** against **−0.023** for
direction. That asymmetry cannot be harvested by buying and selling stock, which
requires direction. It can be harvested through options, and the variance risk
premium has an explicit mechanism: systematic hedging demand for downside
protection. Needs options data and a much more careful risk model, but it is the
only place where this project already has a measured, enormous signal rather
than a hoped-for one.

### The honest caveat on all four

None of these is a promise. Cross-sectional equity factors have decayed
substantially; futures trend has had a poor decade; the variance premium is
crowded and periodically catastrophic. The claim is narrower and is the only
kind the gate permits: **these four have a named mechanism, a named counterparty
with a reason to lose, a reason they are not fully arbitraged, and a cost
structure this project can survive.** That makes them worth the cost of a test.
Everything in Part 1 does not.

## Recommendation

Move to **daily-horizon cross-sectional US equity**. It is the only change that
attacks the binding constraint — a 2.4 bps round trip against an hour of
holding — rather than trying to out-think it. The pipeline, the splits, the
attribution, the shuffle tests and the ledger all carry over; only the bar size
and the universe change.

If the interest is specifically in trend following, the honest home for it is
futures, not intraday equities. The negative result in R04/R05 is about this
venue and should not be read as a verdict on trend as a phenomenon.
