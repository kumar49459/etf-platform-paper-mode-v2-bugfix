"""Limit-order execution strategy (PHASE6_Objectives.md section 7).

Policy Strategy Engine DEFINES, not mechanics it executes - real Kite
interaction is Phase 12. Default: LIMIT orders, not MARKET, for long-term
SIP-style buying, since a long-term investor has no urgency that would
justify accepting an unknown market price, and a limit order bounds the
worst case.
"""

from __future__ import annotations

DEFAULT_LIMIT_PRICE_BUFFER_PCT = 0.003
"""Provisional, disclosed parameter - same honesty standard as Phase 5's
drift tolerance and Phase 4's slippage assumption. Not a researched-optimal
value; needs real paper-trading fill data before being trusted as tuned."""


def compute_limit_price(last_close, buffer_pct=DEFAULT_LIMIT_PRICE_BUFFER_PCT):
    """Anchor to the most recent close with a small conservative buffer to
    improve fill probability without chasing the market - a deliberately
    conservative anchor for a long-term buy-and-hold context, not a tight
    scalping price (PHASE6_Objectives.md section 7)."""
    if last_close <= 0:
        raise ValueError(f"last_close must be positive, got {last_close}")
    if buffer_pct < 0:
        raise ValueError(f"buffer_pct cannot be negative, got {buffer_pct}")
    return round(last_close * (1 + buffer_pct), 2)
