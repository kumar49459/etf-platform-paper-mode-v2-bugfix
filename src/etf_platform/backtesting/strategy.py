"""Strategy interface.

This is the same `Allocator`-style interface concept already committed to
in PHASE1_Architecture_SRS.md §4 ("Strategy Engine and AI Allocation Engine
both implement the same `Allocator` interface... so the Backtesting Engine
and Live Trading system can swap between them without code changes").
Phase 4 implements the backtesting-facing half of that contract.

THE NO-LOOK-AHEAD GUARANTEE (read this before writing a Strategy):
`history[symbol]` passed to `generate_orders()` contains only bars with
`trade_date <= as_of_date`. The engine enforces this — see engine.py's
`_history_up_to()` — it is not something a Strategy needs to self-police,
and there is no way for a Strategy to request or receive a bar dated after
`as_of_date` through this interface. Orders returned from this call are
filled on a LATER date (never `as_of_date` itself) — see engine.py's
sequencing. A Strategy that tries to "trade on today's close using today's
close" is structurally prevented from doing so, not just discouraged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from etf_platform.backtesting.models import OrderIntent, PortfolioSnapshot
from etf_platform.data_engine.models import OHLCVBar


class Strategy(ABC):
    """Base class for anything that decides trades: rule-based strategies
    (Phase 1 roadmap Phase 6) and the AI Dynamic Allocation Engine (Phase 7)
    both implement this same interface, so the Backtesting Engine works
    identically regardless of which is plugged in."""

    @abstractmethod
    def generate_orders(
        self,
        as_of_date: date,
        history: dict[str, list[OHLCVBar]],
        portfolio: PortfolioSnapshot,
    ) -> list[OrderIntent]:
        """Return zero or more OrderIntents based only on `history` (bars up
        to and including `as_of_date`) and the current `portfolio` state.
        Every OrderIntent must carry a non-empty `rationale` — this is
        enforced by OrderIntent's own constructor, not optional here.
        """
