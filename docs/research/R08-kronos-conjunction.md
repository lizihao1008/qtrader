# R08 — Combining the S/R signal with Kronos as a confirmation filter

**Proposal:** take entries from the S/R state machine, confirm them with the
Kronos foundation model, trade only when both agree, and expect higher
profitability.
**Verdict: do not implement.** The conjunction fails on arithmetic before
Kronos is reached, and Kronos alone fails the gate.

## 1. The conjunction cannot help, and this needs no test

Let `A` be the S/R entry condition and `B` the model's confirmation. The claim is
that `E[r | A ∧ B] > E[r | B]`.

That holds only if `A` carries information about `r`. R07 measured it and it does
not: crossing a round number is followed by −0.0035 sigma of continuation against
−0.0030 for placebo offsets — a difference of **0.0006 sigma** over 170,038
crossings, with no |t| above 0.78. Previous-day high/low is bracketed by its own
placebos. `A` is independent of forward returns.

For an `A` independent of `r`,

    E[r | A ∧ B] = E[r | B]

The conjunction has **the same expectancy per trade** as the model alone, on a
smaller sample. It does not raise profitability. What it does:

* cuts the trade count, so gross and cost both fall in proportion — the *rate*
  is unchanged;
* widens the confidence interval on every estimate, because n is smaller;
* introduces a selection whose backtest can look better **or** worse purely by
  chance.

That last point is the real hazard. Adding filters to a losing strategy until
the equity curve improves is one of the standard routes into overfitting, and a
conjunction search is exactly that procedure. The improvement would be a
property of the sample, not of the strategy — which is what the Deflated Sharpe
Ratio and PBO literature, cited in the user's own report, exists to penalise.

**So the question is not "does A + B work". It is "does B work". `A`
contributes nothing and should be dropped from the proposal entirely.**

## 2. Kronos on its own, against the gate

Kronos is a decoder-only transformer over tokenised OHLCV, pretrained on K-line
sequences from 45+ exchanges, in four sizes from 4.1M to 499M parameters. It
outputs **forecast OHLCV paths**, not calibrated expected returns.

| criterion | verdict |
| --- | --- |
| **§1 mechanism** | **Fail.** No economic mechanism is offered, and none is implied by the architecture. "Pretrained on 45 exchanges" is provenance, not a reason any counterparty loses money. A named participant with a non-profit-seeking reason to trade against it does not exist in the description. |
| **§2 not arbitraged** | **Fail.** Public weights, public architecture, free OHLCV inputs. Anything it finds is computable by anyone with a GPU. In intraday US large caps — where this project already runs at 2.1% tape coverage as a slow taker — this is the worst competitive position available. |
| **§3 which side is paid** | **Unchanged fail.** Still marketable orders at the next bar's open. Any structure living inside a 2.4 bps round trip is collected by whoever quotes. A better forecast does not change who gets filled passively. |
| **§4 cost** | **Fail as stated.** No edge magnitude is claimed. Worse, the output is a *price path*, so converting it into a position needs a decision rule with its own free parameters — more researcher degrees of freedom on top of a signal with no stated size. |
| **§5 incremental information** | **Fail, but with an honest qualification** — see below. |

The model's own README is the strongest piece of evidence here. It states that
Kronos is **"not a production-ready quantitative trading system"**, makes no
profitability or backtest claims, and describes its example top-K strategy as
"a basic starting point". The authors are not claiming what the proposal needs
them to have claimed.

### The §5 qualification, stated fairly

§5 rejects reparameterisations of already-tested inputs, and Kronos's inputs are
OHLCV — the class this project has measured repeatedly. But the rule was written
about *inputs*, and Kronos is a genuinely different **function class**. Every
signal tested here — trailing returns, drift z-scores, MACD, residual reversal —
is a hand-crafted, largely linear function. A 500M-parameter model could in
principle extract nonlinear structure those miss. Hiding behind the rule would
be dishonest.

Two things constrain how much room that leaves:

1. **R05's shuffle test is about the process, not about my features.** Real
   time-ordering produces sustained trends at 0.543x the rate of the same
   returns shuffled. That is a statement about the information in the ordering
   itself, which any model — linear or not — has to extract from. It does not
   prove there is no nonlinear structure, but it bounds the prize.
2. **Whatever is found must beat 2.4 bps per round trip.** The strongest effect
   measured anywhere in this project — residual reversal, with rank IC +0.017 at
   t = +9.4 — is worth +1.7 to +4.1 bps gross and does not clear that bar. A
   model would have to find substantially more than the best known effect in
   this universe, using a strict subset of the data everyone else has.

## 3. "Greater than 50% win rate" — what 50% is worth here

A win rate is uninterpretable without a payoff ratio and a cost. This project's
own `trend_ratchet` had a **36.6%** win rate and sat at gross breakeven, because
its payoff ratio was 1.76. So the number to ask for is not "above 50" but "above
what".

For a predictor right `p` of the time whose accuracy is independent of move
size, expectancy per trade is `(2p - 1) * E[|move|]`. Measured on `m5_mine`:

| hold | E[\|move\|] | breakeven p | p for 2x cost | p for a genuinely attractive edge |
| --- | --- | --- | --- | --- |
| 3 bars | 18.8 bps | **56.4%** | 62.8% | 75.6% |
| 6 bars | 25.9 bps | **54.6%** | 59.3% | 68.5% |
| 12 bars | 35.6 bps | **53.4%** | 56.7% | 63.5% |
| 24 bars | 48.6 bps | **52.5%** | 54.9% | 59.9% |

**51% loses money at every horizon.** At the 12-bar hold used throughout this
project, breakeven is 53.4%.

The reference point that matters most:

| signal | gross per trade | implied win rate |
| --- | --- | --- |
| residual reversal — the best thing found in this project, rank IC +0.017 at t = +9.4 | +1.69 bps | **52.37%** |
| `trend_ratchet` 5-minute baseline | −1.03 bps | 48.56% |
| **breakeven at 2.4 bps cost** | +2.40 bps | **53.37%** |

A signal with a t-statistic of +9.4 on its information coefficient — statistically
overwhelming — converts to a **52.4%** win rate, and that is **below breakeven**.
"Greater than 50%" is therefore claiming something weaker than a signal this
project already measured, validated statistically, and rejected on cost.

And the claim as relayed is unsourced. To be assessable at all it would need to
state: which market, which bar frequency, which horizon, in-sample or out,
before or after costs, and win rate *of what* — the direction of a forecast path,
or the P&L of an executable position. Those are different numbers, and public
demonstrations of this class of model are frequently on crypto, where both the
cost structure and the dynamics differ from US equities.

## 4. "Would it filter out the S/R false signals?"

The phrase presupposes what R07 disproved. "False signals" only exists as a
category if there are also *true* signals to separate them from. With the S/R
leg measured at 0.0006 sigma against placebo, every S/R trigger is equally
uninformative — there is no correct subset for a filter to recover.

Formally: if `A` is independent of `r`, then `{A = 1}` is, with respect to `r`, a
random subset of bars. Applying `B` inside a random subset yields `B`'s edge on
that subset, equal in expectation to `B`'s edge everywhere.

Which gives the decisive form of the argument — the conjunction is weakly worse
than `B` alone in **both** branches:

* **if Kronos has edge**, you should trade it on every bar, not only where S/R
  happens to fire. Restricting to S/R bars discards Kronos's other opportunities
  at the same expectancy per trade — strictly less total profit;
* **if Kronos has no edge**, S/R cannot supply one.

A conjunction only helps when *both* legs carry information and their errors are
imperfectly correlated — the ordinary ensemble argument. It requires `A` to have
edge, which is the thing that was measured and is absent.

The remaining steelman is that S/R might work only in some regime, with the
measured zero being an average of positive and negative conditions. Possible in
principle, but recovering it means searching for the conditioner — and that
search is the data-mining route the gate exists to block. The same conditioners
were already tried on the reversal signal (dispersion, time of day, volume) and
none concentrated the edge.

## 5. What would change the answer, and what it would cost

The rejection rests on §1 and §2, which no experiment can repair: a signal with
no mechanism, computable by anyone from free data, in the arena where this
project is structurally the paying side. Those are the reasons not to run it.

If the test is wanted anyway — as a check on the §5 reasoning rather than as a
strategy — the decisive version is cheap in concept and should be
pre-registered:

* sample ~1,000 timestamps stratified across `m5_mine`, all 22 symbols at each
  (~22,000 windows);
* run Kronos inference, take the implied H-bar return from the forecast path;
* compute cross-sectional rank IC against realised returns **with a one-bar
  implementation lag** — the test that killed 92% of the reversal signal;
* falsify if IC is not comfortably above the +0.017 that residual reversal
  already achieves, since anything less is a worse version of a signal that
  already fails on cost.

Setup cost: install PyTorch with MPS (this machine is an M3 Pro, no CUDA), pull
the pretrained weights, and build a batched inference harness. Half a day, most
of it plumbing. The measurement framework — IC by implementation lag,
non-overlapping significance, the ledger — is already in place and would not
need changing.

**Recommendation: do not run it.** Not because the result is certain, but
because §1 and §2 mean a positive result would not be actionable: an edge with
no mechanism, available to everyone from public weights and free data, captured
by a slow taker, is not one this project could hold. The right use of half a day
is the daily-horizon pivot in R06, where the cost structure changes by an order
of magnitude and the mechanisms have names.
