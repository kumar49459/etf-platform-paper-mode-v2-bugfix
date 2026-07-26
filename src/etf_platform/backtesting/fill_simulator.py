"""Fill simulator — realistic execution modeling (Phase 4 objective #5).

Fill price model:
- MARKET orders fill at the execution bar's OPEN, never its close. Filling
  at the same bar's close (the bar the decision was based on) would be a
  classic look-ahead bug; filling at the *next* bar's open is the standard,
  conservative, realistic assumption — you can't react to a bar's own close
  before it happens, but you can act on the next bar's open once it does.
- LIMIT orders fill only if the execution bar's range actually touches the
  limit price (buy: bar low <= limit; sell: bar high >= limit) — never
  assumed filled just because the strategy wanted it to be. If the bar gaps
  through the limit in the trader's favor (e.g. a buy limit of 100 but the
  bar opens at 95), the fill price is the more favorable OPEN, not the
  limit — this is how real limit orders behave, not an optimistic
  assumption. If unfilled, the order remains pending until `expiry_date`.

Slippage is modeled as a cost line item (CostBreakdown.slippage_cost, an
INR amount), not as a price adjustment — this keeps the recorded fill_price
an honest "what price did the market actually print" figure, with the cost
of crossing the spread accounted separately and explicitly, rather than
silently baked into a fudged price.
"""

from __future__ import annotations

from etf_platform.backtesting.models import Fill, OrderType, PendingOrder
from etf_platform.common.logging_setup import get_logger
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import OHLCVBar

logger = get_logger("backtesting.fill_simulator")


class FillSimulator:
    def __init__(self, cost_tax_engine: CostTaxEngine, max_volume_participation_pct: float | None = None) -> None:
        """`max_volume_participation_pct`, if set (e.g. 0.1 for 10%), caps
        any single fill at that fraction of the execution bar's traded
        volume — modeling that a large order relative to a day's liquidity
        cannot realistically fill in full on one day. Defaults to None
        (unlimited fill, the original Phase 4 behavior) so existing
        callers and the locked regression baseline are unaffected unless
        this is explicitly opted into.
        """
        if max_volume_participation_pct is not None and not (0 < max_volume_participation_pct <= 1.0):
            raise ValueError(
                f"max_volume_participation_pct must be in (0, 1], got {max_volume_participation_pct}."
            )
        self._cost_tax_engine = cost_tax_engine
        self._max_participation = max_volume_participation_pct

    def try_fill(self, pending: PendingOrder, execution_bar: OHLCVBar) -> Fill | None:
        """Attempt to fill `pending` against `execution_bar`. The caller
        (engine.py) is responsible for only invoking this with a bar dated
        on or after `pending.target_fill_date` — this method does not
        itself check dates, since it has no notion of "today" independent
        of the bar it's given.

        If `max_volume_participation_pct` is configured and the requested
        quantity exceeds that cap, the returned Fill's quantity is reduced
        to the capped amount (a PARTIAL fill) — the caller is responsible
        for re-queuing the unfilled remainder (see engine.py's
        `_process_pending_orders`).
        """
        intent = pending.intent

        if intent.order_type == OrderType.MARKET:
            fill_price = execution_bar.open
        else:
            fill_price = self._limit_fill_price(intent.side, intent.limit_price, execution_bar)
            if fill_price is None:
                return None

        fill_quantity = intent.quantity
        if self._max_participation is not None:
            cap = execution_bar.volume * self._max_participation
            if fill_quantity > cap:
                fill_quantity = max(0.0, float(int(cap)))  # whole units only, see OrderIntent's own constraint
                if fill_quantity <= 0:
                    return None  # bar has essentially no liquidity relative to the order — no fill at all today

        cost = self._cost_tax_engine.compute_transaction_cost(intent.side, fill_price, fill_quantity)
        fill = Fill(
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=fill_quantity,
            fill_price=fill_price,
            fill_date=execution_bar.trade_date,
            decided_date=pending.decided_date,
            cost=cost,
            rationale=intent.rationale,
        )
        if fill_quantity < intent.quantity:
            logger.info(
                "Partial fill for %s %s: requested %.4f, filled %.4f (volume participation cap).",
                intent.side.value, intent.symbol, intent.quantity, fill_quantity,
            )
        else:
            logger.debug(
                "Filled %s %s %.4f @ %.2f on %s (decided %s)",
                intent.side.value, intent.symbol, intent.quantity, fill_price,
                execution_bar.trade_date, pending.decided_date,
            )
        return fill

    @staticmethod
    def _limit_fill_price(side: Side, limit_price: float, bar: OHLCVBar) -> float | None:
        if side == Side.BUY:
            if bar.low > limit_price:
                return None
            return min(limit_price, bar.open)
        else:
            if bar.high < limit_price:
                return None
            return max(limit_price, bar.open)
