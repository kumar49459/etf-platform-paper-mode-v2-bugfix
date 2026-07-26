"""Core data models for the Portfolio Cash & Execution Manager (Module 28).

See PHASE7_Objectives.md section 4 for the full state machine design and
PHASE7_Design_Readiness_Review.md for the findings that shaped several of
these models (priority preservation, concurrent-invocation claims,
corruption recovery).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from etf_platform.execution_manager.exceptions import InvalidLifecycleTransitionError
from etf_platform.execution_manager.timezone_utils import require_aware


class OrderLifecycleState(Enum):
    """Canonical state names, exactly as specified in PHASE7_Objectives.md
    section 4 - adopted verbatim, not paraphrased.

    AMBIGUOUS added per DDR-001: when reconciliation cannot conclusively
    determine an order's broker-side fate (the record has no local
    broker_order_id and no match is found among the broker's currently-
    open orders -- which structurally cannot rule out "reached the broker
    and already resolved to a terminal state before we could check"),
    the record is escalated here rather than guessed back to VERIFIED for
    automatic retry. This state is TERMINAL for automated processing --
    no automatic code path transitions anything out of it. The only way
    out is an explicit, human-invoked resolution action (see
    reconciliation.py's resolve_ambiguous_execution()), never a scheduled
    or automatic one.
    """

    PROPOSAL = "proposal"
    VERIFIED = "verified"
    SUBMITTED = "submitted"
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RECONCILED = "reconciled"
    AMBIGUOUS = "ambiguous"


ORDER_LIFECYCLE_TRANSITIONS = {
    OrderLifecycleState.PROPOSAL: frozenset({OrderLifecycleState.VERIFIED, OrderLifecycleState.FAILED}),
    OrderLifecycleState.VERIFIED: frozenset({OrderLifecycleState.SUBMITTED, OrderLifecycleState.FAILED}),
    OrderLifecycleState.SUBMITTED: frozenset({
        OrderLifecycleState.PENDING, OrderLifecycleState.FAILED,
        OrderLifecycleState.VERIFIED,  # retry edge -- ONLY used by ReconciliationService, after confirming
                                        # via get_open_orders() client_reference matching that the broker
                                        # genuinely never received this order (found during Milestone 3's own
                                        # crash-recovery testing -- see reconciliation.py)
        OrderLifecycleState.AMBIGUOUS,  # DDR-001 escalation edge -- used ONLY when reconciliation cannot
                                         # confirm either outcome (found the order) or safety (confirmed
                                         # absent); replaces the old, unsafe default of guessing VERIFIED.
    }),
    OrderLifecycleState.PENDING: frozenset({
        OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED, OrderLifecycleState.PENDING,
    }),
    OrderLifecycleState.PARTIALLY_FILLED: frozenset({
        OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED,
    }),
    OrderLifecycleState.FILLED: frozenset({OrderLifecycleState.RECONCILED}),
    OrderLifecycleState.CANCELLED: frozenset({OrderLifecycleState.RECONCILED}),
    OrderLifecycleState.FAILED: frozenset({OrderLifecycleState.RECONCILED}),
    OrderLifecycleState.RECONCILED: frozenset(),
    OrderLifecycleState.AMBIGUOUS: frozenset({
        # Every one of these is reachable ONLY via resolve_ambiguous_execution()
        # (an explicit, human-invoked action -- see reconciliation.py), never
        # by any automatic/scheduled code path. Listed exhaustively because an
        # operator resolving an ambiguous execution may discover any of these
        # underlying truths once they've actually checked the broker directly.
        OrderLifecycleState.VERIFIED,   # operator confirmed: broker genuinely never received it, safe to retry
        OrderLifecycleState.PENDING,    # operator confirmed: order is live at the broker
        OrderLifecycleState.PARTIALLY_FILLED,
        OrderLifecycleState.FILLED,     # operator confirmed: order filled at the broker
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.FAILED,     # operator confirmed: order was rejected
        OrderLifecycleState.RECONCILED, # operator manually closed this out with no further action needed
    }),
}


def validate_transition(current, target):
    allowed = ORDER_LIFECYCLE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidLifecycleTransitionError(
            f"Cannot transition from {current.value} to {target.value}. "
            f"Valid transitions from {current.value}: {sorted(s.value for s in allowed) or '(none -- terminal)'}"
        )


class ComplianceCheckResult(Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class ComplianceResult:
    """Output of a ComplianceCheckPort.check() call (PHASE7_Objectives.md
    section 8.4's found-and-fixed abstraction)."""

    result: ComplianceCheckResult
    reason: str = None


@dataclass(frozen=True)
class MarketDepthSnapshot:
    """Output of LiveQuoteProvider.get_market_depth() - used for
    liquidity protection (PHASE7_Objectives.md section 8.5)."""

    symbol: str
    as_of: object
    bid_price: float
    ask_price: float
    bid_quantity: int
    ask_quantity: int

    def __post_init__(self):
        require_aware(self.as_of, "as_of")

    @property
    def spread_pct(self):
        if self.bid_price <= 0:
            return float("inf")
        return (self.ask_price - self.bid_price) / self.bid_price


@dataclass(frozen=True)
class CycleClaim:
    """The concurrent-invocation mutual-exclusion record
    (PHASE7_Design_Readiness_Review.md section 8.8) - a second invocation
    observing an existing, unexpired claim for the same cycle_id must not
    proceed."""

    cycle_id: str
    claimed_at: object
    claimed_by: str

    def __post_init__(self):
        require_aware(self.claimed_at, "claimed_at")


@dataclass
class ExecutionRecord:
    """One row of the execution_history table (PHASE7_Objectives.md
    section 5) - the full lifecycle record for a single proposed order,
    from PROPOSAL through its eventual RECONCILED closure."""

    execution_id: str
    queue_id: object
    cycle_id: str
    symbol: str
    quantity_proposed: int
    quantity_final: object
    limit_price: float
    order_status: OrderLifecycleState
    broker_order_id: object
    executed_price: object
    executed_quantity: int
    is_paper_trade: bool
    created_at: object
    last_status_check: object
    priority_rank: int
    notes: tuple = field(default_factory=tuple)

    def __post_init__(self):
        require_aware(self.created_at, "created_at")
        if self.last_status_check is not None:
            require_aware(self.last_status_check, "last_status_check")

    def transition_to(self, new_status):
        validate_transition(self.order_status, new_status)
        self.order_status = new_status
