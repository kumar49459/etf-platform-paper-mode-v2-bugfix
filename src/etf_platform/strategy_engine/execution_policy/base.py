"""ExecutionPolicy interface (PHASE6_Objectives.md section 0.2/3) - the
pluggable Recurring-Monthly-vs-Lump-Sum distinction, folded into Phase 6
as an internal component per the approved resolution. Same registry
pattern already used for portfolio_optimizer.methods.AllocationMethod
(Phase 5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ExecutionPolicy(ABC):
    """Both Recurring Monthly and Lump Sum policies produce the same kind
    of output (a decision about whether/how much capital is ready to
    deploy today) - they differ only in WHEN they're invoked and HOW MUCH
    new_capital they're given, never in the underlying buy-only allocation
    logic (priority.py), which both hand off to identically.

    IMPORTANT (production verification review, see CHANGELOG.md):
    run_cycle() must NEVER call notification_port.send() or
    cash_ledger_port.notify_expected_contribution() directly -- it decides
    that these are needed and returns that decision as a PendingSideEffects
    value; the caller (strategy.py) is responsible for persisting the
    resulting state BEFORE performing them. This is what makes "state
    persisted before any external side effect" a structural guarantee
    rather than a small, hopefully-safe crash window. get_available_pool()
    (a READ, not a write) is exempt -- it's idempotent and safe to repeat,
    so there's no duplication risk in calling it before persistence."""

    @abstractmethod
    def run_cycle(self, as_of_date, state, cash_ledger_port, notification_port):
        """Returns (updated_state, pool_if_ready_to_execute_else_None, notes, pending_side_effects)."""

    @abstractmethod
    def mark_cycle_complete(self, state, any_orders_produced):
        """Called after the core allocation logic has run against the pool
        this policy returned. any_orders_produced=False means not even
        the highest-priority opportunity could be funded (e.g. capital too
        small to buy one whole unit) - the policy decides whether that
        counts as "handled" (-> IDLE) or "still waiting" (stays active)."""
