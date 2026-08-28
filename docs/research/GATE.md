# The gate: what an idea must survive before any code is written

This project spent a lot of effort backtesting ideas that should have been
rejected on paper. The measurement framework that came out of it is sound, and
the conclusions it reached were honest, but the *selection* of what to measure
was not disciplined: MACD crossovers, moving-average crossovers and a
long/short reversal book all reached full implementation without anyone first
asking whether they had a reason to work.

From now on an idea is written down against this list **before** a line of
strategy code exists. Failing any of the first four is a rejection, not a
caveat to note and proceed anyway.

## 1. Mechanism

*Who is on the other side, and why are they willing to lose?*

There must be a named participant with a non-profit-seeking reason to trade
against you: a forced seller, a hedger paying for insurance, an index fund
rebalancing on a schedule, a retail flow buying at market, a risk-limit
liquidation. "The pattern appears in the data" is not a mechanism. "Prices
overreact" is not a mechanism unless it names who overreacts and why they
cannot stop.

Reject if the answer is a description of the price series rather than of a
participant.

## 2. Why has it not been arbitraged away

*What stops the obvious competitor from taking it first?*

Acceptable answers: a capacity limit that makes it uninteresting to large
capital; a balance-sheet or regulatory constraint others face; genuinely costly
data; a risk nobody wants to hold. Unacceptable: "it is subtle", "few people
look at 5-minute bars", "my parameterisation is different".

## 3. Which side of the trade gets paid — and can I be on it

*This is the question this project kept failing.*

Many real effects exist and are paid to a specific role, not to anyone who
notices them. Short-term reversal compensates whoever is **quoting** — providing
liquidity and carrying inventory. Harvesting it as a liquidity **taker**, by
sending marketable orders that cross the spread, means paying the very
compensation the effect consists of. The edge is not missing; it is being paid
to someone else.

So: does capturing this require passive execution, speed, or a venue I do not
have? With marketable orders filled at the next bar's open, any effect that
lives inside the spread is inaccessible by construction.

## 4. Survives realistic cost, with the number written down first

State the expected edge per round trip *before* backtesting, and the cost. In
this setup the cost is ~2.4 bps per round trip (Roll-implied 0.68 bps half
spread plus 0.5 bps slippage, both legs). An idea whose expected edge is not
several times that has no margin for being wrong.

Reject if the honest pre-estimate is within 2x of costs.

## 5. Incremental information

*Is this a new input, or a monotone transform of one already tested?*

MACD, moving-average crossovers, momentum z-scores, breakout rules and
oscillator variants are all functions of past prices over a window. Testing
another one is not a new experiment. An idea earns a test by bringing an input
the project has not had: order flow, positioning, calendar/flow events,
fundamentals, cross-asset state.

Reject if it is a reparameterisation.

## 6. Regime and capacity

Under what conditions should it fail? An idea that cannot name its own failure
regime has not been thought through. And at what size does it stop working —
if the answer is unknown, the mechanism is not understood.

## 7. Pre-registration

Before running anything: the hypothesis in one sentence, the expected sign and
magnitude, the sample it will be tested on, and what result would falsify it.
Recorded in `results/search/ledger.jsonl` with the trial, so the
multiple-testing burden stays a fact rather than a memory.

---

## Retroactive verdicts on what is already in this repository

| strategy | mechanism | crowding | which side pays | incremental | verdict |
| --- | --- | --- | --- | --- | --- |
| `ma_cross` | none | saturated | n/a | no — price transform | **Not a strategy.** Retain only as a plumbing fixture; never a candidate. |
| `trend_ratchet` | none *at this horizon* | saturated | taker | no — price transform | **Rejected.** Trend has a real mechanism at multi-month horizons (underreaction, flows); at 5 minutes it has none, and IC is −0.023 (t = −11.2). Measured wrong-signed, not merely unprofitable. |
| `cross_sectional_residual` | real: inventory / liquidity provision | extremely | **taker — wrong side** | no | **Rejected on mechanism.** The effect compensates quoting, not crossing. See below. |
| volatility forecasting (IC +0.67) | real: volatility clustering | universally known | n/a | no | **Not an alpha.** A stylised fact every participant models. Keep as *infrastructure* — sizing, stop distance, no-trade decisions — never as a signal. |

Nothing currently in the repository passes the gate.

### The reversal case, in detail — and one thing that does not fit

Short-term cross-sectional reversal has a genuine mechanism: someone absorbing
an order-flow imbalance carries inventory risk and is paid for it. That predicts
the effect is real (it is: +2.9 to +4.1 bps, t up to +2.9) *and* that it accrues
to the liquidity provider. Measured edge came out at roughly the size of the
round-trip spread, which is what the mechanism predicts for someone on the
taking side.

But one measurement does **not** fit that story. If the compensation is for
carrying inventory, the edge should scale with the spread — wider spread, more
inventory risk, more compensation. It does not:

| | median Roll half-spread | reversal edge |
| --- | --- | --- |
| cheap half of the universe | 1.04 bps | +2.90 bps |
| expensive half | 2.42 bps | +3.30 bps |

Spread more than doubles; edge moves 14%. So the pure inventory story is *not*
well supported by this data, which under §1 makes the mechanism weaker than it
first appeared — and a weak mechanism is a rejection, not a footnote. Either the
IEX-visible spread is a poor proxy for the real one, or the effect is something
else. Both readings argue against building on it.

---

## What this implies about the venue, not just the ideas

The harder conclusion is that the binding constraint here is not idea quality.

Intraday US large-cap equities is the most heavily competed arena in the
business. Every competitor has SIP or direct feeds where this project has IEX —
a few percent of the tape. Every competitor quotes where this project crosses.
Any effect that lives inside a 2.4 bps round trip is, by construction, being
collected by someone with better data, better execution and a passive fill.

Under §3, that is not a handicap to overcome with a cleverer signal. It is a
structural reason to expect the answer to keep coming back negative — which it
has, three times now, by three different methods.

The productive responses are to change the venue, not the parameterisation:

* **Longer horizons**, where latency and spread stop dominating and the
  competition is different capital with different constraints;
* **Non-price inputs**, where the data itself is the barrier rather than the
  processing of it;
* **Less-competed instruments**, where the count of participants with better
  infrastructure is smaller.

None of these is a strategy. They are the only directions in which proposing one
would not be a waste of research time.
