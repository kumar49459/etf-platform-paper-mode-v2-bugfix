"""VerificationService (Milestone 3, requirement 1) -- a PURE validation
component. It never modifies orders, never creates execution decisions,
and contains no business logic of its own: it checks affordability,
compliance, liquidity, order validity, and execution prerequisites against
live data, and returns a structured, immutable result. The caller
(SubmissionOrchestrator) decides what to do with that result -- this
service never mutates anything, including its own inputs.

Reuses CostTaxEngine (Phase 4, frozen) for affordability -- the exact same
"largest affordable whole-unit quantity given the full cost breakdown"
logic Strategy Engine's own _affordable_quantity already established,
re-run here against LIVE inputs rather than proposal-time estimates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from etf_platform.cost_tax_engine import CostTaxEngine, Side


class VerificationOutcome(Enum):
    APPROVED = "approved"
    APPROVED_WITH_REDUCED_QUANTITY = "approved_with_reduced_quantity"
    REJECTED = "rejected"


class RejectionReason(Enum):
    INSUFFICIENT_CASH = "insufficient_cash"
    COMPLIANCE_FAILED = "compliance_failed"
    LIQUIDITY_INSUFFICIENT = "liquidity_insufficient"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    INVALID_ORDER = "invalid_order"


@dataclass(frozen=True)
class VerificationResult:
    outcome: VerificationOutcome
    verified_quantity: int
    original_quantity: int
    limit_price: float
    rejection_reason: RejectionReason = None
    notes: tuple = field(default_factory=tuple)

    @property
    def approved(self):
        return self.outcome in (VerificationOutcome.APPROVED, VerificationOutcome.APPROVED_WITH_REDUCED_QUANTITY)


DEFAULT_MAX_SPREAD_PCT = 0.02
"""Provisional, disclosed liquidity threshold - same honesty standard as
every other provisional parameter in this platform."""


class VerificationService:
    def __init__(self, cost_tax_engine=None, max_spread_pct=DEFAULT_MAX_SPREAD_PCT):
        self._cost_tax_engine = cost_tax_engine or CostTaxEngine()
        self._max_spread_pct = max_spread_pct

    def verify(self, symbol, proposed_quantity, limit_price, live_quote_provider, broker_port, compliance_port):
        if proposed_quantity <= 0 or limit_price <= 0:
            return VerificationResult(
                outcome=VerificationOutcome.REJECTED, verified_quantity=0, original_quantity=proposed_quantity,
                limit_price=limit_price, rejection_reason=RejectionReason.INVALID_ORDER,
                notes=(f"Non-positive quantity ({proposed_quantity}) or price ({limit_price}).",),
            )

        from etf_platform.execution_manager.models import ComplianceCheckResult

        compliance_result = compliance_port.check(symbol, proposed_quantity, limit_price)
        if compliance_result.result == ComplianceCheckResult.FAIL:
            return VerificationResult(
                outcome=VerificationOutcome.REJECTED, verified_quantity=0, original_quantity=proposed_quantity,
                limit_price=limit_price, rejection_reason=RejectionReason.COMPLIANCE_FAILED,
                notes=(compliance_result.reason or "Compliance check failed.",),
            )

        depth = live_quote_provider.get_market_depth(symbol)
        if depth is None:
            return VerificationResult(
                outcome=VerificationOutcome.REJECTED, verified_quantity=0, original_quantity=proposed_quantity,
                limit_price=limit_price, rejection_reason=RejectionReason.QUOTE_UNAVAILABLE,
                notes=(f"No market depth available for {symbol}.",),
            )
        if depth.spread_pct > self._max_spread_pct:
            return VerificationResult(
                outcome=VerificationOutcome.REJECTED, verified_quantity=0, original_quantity=proposed_quantity,
                limit_price=limit_price, rejection_reason=RejectionReason.LIQUIDITY_INSUFFICIENT,
                notes=(f"Spread {depth.spread_pct:.2%} exceeds max {self._max_spread_pct:.2%}.",),
            )

        available_cash = broker_port.get_available_cash()
        verified_quantity = self._affordable_quantity(limit_price, available_cash, proposed_quantity)
        if verified_quantity <= 0:
            return VerificationResult(
                outcome=VerificationOutcome.REJECTED, verified_quantity=0, original_quantity=proposed_quantity,
                limit_price=limit_price, rejection_reason=RejectionReason.INSUFFICIENT_CASH,
                notes=(f"Available cash {available_cash:.2f} insufficient for even 1 unit at {limit_price:.2f}.",),
            )

        verified_quantity = min(verified_quantity, proposed_quantity)

        outcome = (
            VerificationOutcome.APPROVED if verified_quantity == proposed_quantity
            else VerificationOutcome.APPROVED_WITH_REDUCED_QUANTITY
        )
        notes = ()
        if verified_quantity < proposed_quantity:
            notes = (f"Quantity reduced from {proposed_quantity} to {verified_quantity} (live cash/cost check).",)
        return VerificationResult(
            outcome=outcome, verified_quantity=verified_quantity, original_quantity=proposed_quantity,
            limit_price=limit_price, notes=notes,
        )

    def _affordable_quantity(self, price, budget, proposed_quantity):
        if price <= 0 or budget <= 0:
            return 0
        quantity = min(int(budget / price), proposed_quantity)
        while quantity > 0:
            cost = self._cost_tax_engine.compute_transaction_cost(Side.BUY, price, quantity)
            if quantity * price + cost.total_cost <= budget:
                return quantity
            quantity -= 1
        return 0
