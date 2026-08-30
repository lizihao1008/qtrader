"""Turning a snapshot into the text the model sees, and versioning it.

The prompt is content-addressed: `PROMPT_VERSION` is a hash of the template, so
a journal row can never be misattributed to a prompt it was not produced by.
Edit the template and the version changes on its own.
"""

from __future__ import annotations

import hashlib

from ..data.sessions import session_date, to_market_time

TEMPLATE = """You are a trading-setup validator inside an automated intraday system.
A quantitative strategy has already proposed a trade. You do NOT decide position
size and you cannot propose a different trade. Your only job is to judge whether
the market context supports the proposal.

PROPOSAL
  symbol      {symbol}
  direction   {side}
  time        {clock} (bar {bar_of_session} of the session)

STRATEGY'S OWN MEASUREMENTS AT THIS BAR
{indicators}

SESSION CONTEXT
{session}

EXECUTION TIMEFRAME (exchange-local time, most recent last)
{execution}

HIGHER TIMEFRAME (same window, aggregated {coarse_factor}x, most recent last)
{coarse}

A chart of the same window is attached. Everything you can see ends at the
decision bar; there is no data after it, so do not speculate about what happened
next and do not read prices off the image that are not listed above.

JUDGE
1. the current market regime;
2. whether the proposed {side} is supported by the context, or contradicted by it;
3. whether the evidence is consistent — trend direction, volume behaviour,
   where price sits against the session range, and whether a move looks like
   continuation or exhaustion;
4. any clear contradiction between the strategy's measurements and the price action.

Report a separate confidence for long, for short, and for standing aside. These
are your own directional convictions, not a score for the proposal — if you
think the correct trade is the opposite of the proposal, say so by putting your
confidence on that side. Set supports_setup to false if the context contradicts
the proposal, whatever your confidences are.
"""

PROMPT_VERSION = hashlib.sha256(TEMPLATE.encode()).hexdigest()[:12]


def build(snapshot, *, coarse_factor: int) -> str:
    """Render the template for one snapshot."""
    side = "LONG" if snapshot.direction > 0 else "SHORT"
    return TEMPLATE.format(
        symbol=snapshot.symbol,
        side=side,
        clock=snapshot.session.get("clock", "unknown"),
        bar_of_session=snapshot.session.get("bar_of_session", "?"),
        indicators=_pairs({**snapshot.indicators, **snapshot.features}),
        session=_pairs(snapshot.session),
        execution=_bars(snapshot.bars, limit=30),
        coarse=_bars(snapshot.coarse, limit=12),
        coarse_factor=coarse_factor,
    )


def _pairs(payload: dict) -> str:
    rows = [
        f"  {name:<28} {value:.4f}" if isinstance(value, float) else f"  {name:<28} {value}"
        for name, value in payload.items()
        if value is not None and not isinstance(value, bool)
    ]
    return "\n".join(rows) if rows else "  (none available)"


def _bars(frame, *, limit: int) -> str:
    """Bars in exchange-local time, with any overnight gap called out.

    Two details the model cannot recover on its own: the timestamps are stored
    in UTC but every other number in the prompt is quoted against the exchange
    session, and a 60-bar window early in a session reaches back into the
    previous one. An unmarked boundary reads as continuous trading across a gap
    that may be larger than everything else in the window.
    """
    frame = frame.dropna(subset=["open", "high", "low", "close"]).tail(limit)
    if frame.empty:
        return "  (no bars)"

    local = to_market_time(frame.index)
    day = session_date(frame.index).to_numpy()
    rows = ["  date/time      open     high     low      close    volume"]
    previous = None
    for i, (stamp, row) in enumerate(frame.iterrows()):
        if previous is not None and day[i] != previous:
            rows.append("  ---- overnight gap; the bars below are the current session ----")
        previous = day[i]
        rows.append(
            f"  {local[i].strftime('%m-%d %H:%M')}  {row['open']:8.2f} {row['high']:8.2f} "
            f"{row['low']:8.2f} {row['close']:8.2f} {row['volume']:>10,.0f}"
        )
    return "\n".join(rows)
