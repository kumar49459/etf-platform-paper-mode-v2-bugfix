"""Cost & Tax Engine models (Phase 1 Module 18, implemented here in Phase 4
because the Backtesting Engine has a hard dependency on it — see this
package's __init__.py for the full placement rationale).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class GainType(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


@dataclass(frozen=True)
class CostBreakdown:
    """Full itemized cost of one transaction, in INR. Every field is broken
    out individually (not just a total) so a trade's explanation can say
    exactly where the money went — this feeds directly into the Trade
    explanation requirement in backtesting/models.py."""

    gross_amount: float
    brokerage: float
    stt: float
    stamp_duty: float
    exchange_txn_charge: float
    sebi_turnover_fee: float
    gst: float
    slippage_cost: float

    @property
    def total_cost(self) -> float:
        return (
            self.brokerage + self.stt + self.stamp_duty + self.exchange_txn_charge
            + self.sebi_turnover_fee + self.gst + self.slippage_cost
        )


@dataclass(frozen=True)
class TaxLot:
    """One FIFO-tracked purchase lot for one symbol, used both for capital
    gains classification and for realized win/loss P&L in the trade log."""

    symbol: str
    buy_date: date
    quantity: float
    buy_price: float
    buy_cost_breakdown: CostBreakdown


@dataclass(frozen=True)
class RealizedGain:
    """Result of matching a sell against one or more FIFO tax lots."""

    symbol: str
    sell_date: date
    quantity: float
    buy_date: date  # of the specific lot matched (a multi-lot sell produces multiple RealizedGain records)
    buy_price: float
    sell_price: float
    gain_type: GainType
    gross_gain: float
    tax_rate: float
    estimated_tax: float
    holding_period_days: int
