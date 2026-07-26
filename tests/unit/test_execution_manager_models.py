"""Tests for execution_manager.models - the nine-state order lifecycle."""

from __future__ import annotations

import unittest

from etf_platform.execution_manager import (
    ORDER_LIFECYCLE_TRANSITIONS,
    ExecutionRecord,
    InvalidLifecycleTransitionError,
    OrderLifecycleState,
    utc_now,
    validate_transition,
)


class TestValidTransitions(unittest.TestCase):
    def test_proposal_to_verified_allowed(self):
        validate_transition(OrderLifecycleState.PROPOSAL, OrderLifecycleState.VERIFIED)

    def test_proposal_to_failed_allowed(self):
        validate_transition(OrderLifecycleState.PROPOSAL, OrderLifecycleState.FAILED)

    def test_verified_to_submitted_allowed(self):
        validate_transition(OrderLifecycleState.VERIFIED, OrderLifecycleState.SUBMITTED)

    def test_submitted_to_pending_allowed(self):
        validate_transition(OrderLifecycleState.SUBMITTED, OrderLifecycleState.PENDING)

    def test_pending_to_partially_filled_allowed(self):
        validate_transition(OrderLifecycleState.PENDING, OrderLifecycleState.PARTIALLY_FILLED)

    def test_pending_to_filled_allowed(self):
        validate_transition(OrderLifecycleState.PENDING, OrderLifecycleState.FILLED)

    def test_pending_to_cancelled_allowed(self):
        validate_transition(OrderLifecycleState.PENDING, OrderLifecycleState.CANCELLED)

    def test_pending_self_loop_allowed(self):
        validate_transition(OrderLifecycleState.PENDING, OrderLifecycleState.PENDING)

    def test_partially_filled_to_filled_allowed(self):
        validate_transition(OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.FILLED)

    def test_partially_filled_self_loop_allowed(self):
        validate_transition(OrderLifecycleState.PARTIALLY_FILLED, OrderLifecycleState.PARTIALLY_FILLED)

    def test_filled_to_reconciled_allowed(self):
        validate_transition(OrderLifecycleState.FILLED, OrderLifecycleState.RECONCILED)

    def test_cancelled_to_reconciled_allowed(self):
        validate_transition(OrderLifecycleState.CANCELLED, OrderLifecycleState.RECONCILED)

    def test_failed_to_reconciled_allowed(self):
        validate_transition(OrderLifecycleState.FAILED, OrderLifecycleState.RECONCILED)


class TestInvalidTransitions(unittest.TestCase):
    def test_proposal_cannot_skip_to_filled(self):
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(OrderLifecycleState.PROPOSAL, OrderLifecycleState.FILLED)

    def test_proposal_cannot_skip_to_pending(self):
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(OrderLifecycleState.PROPOSAL, OrderLifecycleState.PENDING)

    def test_reconciled_is_terminal_no_further_transitions(self):
        for target in OrderLifecycleState:
            with self.assertRaises(InvalidLifecycleTransitionError):
                validate_transition(OrderLifecycleState.RECONCILED, target)

    def test_cannot_go_backward_filled_to_pending(self):
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(OrderLifecycleState.FILLED, OrderLifecycleState.PENDING)

    def test_cannot_go_backward_cancelled_to_verified(self):
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(OrderLifecycleState.CANCELLED, OrderLifecycleState.VERIFIED)

    def test_failed_cannot_transition_to_filled(self):
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(OrderLifecycleState.FAILED, OrderLifecycleState.FILLED)

    def test_every_state_has_a_defined_transition_set(self):
        for state in OrderLifecycleState:
            self.assertIn(state, ORDER_LIFECYCLE_TRANSITIONS)

    def test_every_non_reconciled_state_can_eventually_reach_reconciled(self):
        for state in OrderLifecycleState:
            if state == OrderLifecycleState.RECONCILED:
                continue
            self.assertTrue(
                len(ORDER_LIFECYCLE_TRANSITIONS[state]) > 0,
                f"{state} has no outgoing transitions at all -- a dead end.",
            )


class TestExecutionRecordTransitions(unittest.TestCase):
    def _make_record(self, status=OrderLifecycleState.PROPOSAL):
        return ExecutionRecord(
            execution_id="e1", queue_id=None, cycle_id="c1", symbol="A", quantity_proposed=10,
            quantity_final=None, limit_price=100.0, order_status=status, broker_order_id=None,
            executed_price=None, executed_quantity=0, is_paper_trade=True, created_at=utc_now(),
            last_status_check=None, priority_rank=1,
        )

    def test_transition_to_updates_status(self):
        record = self._make_record()
        record.transition_to(OrderLifecycleState.VERIFIED)
        self.assertEqual(record.order_status, OrderLifecycleState.VERIFIED)

    def test_transition_to_rejects_invalid_transition(self):
        record = self._make_record()
        with self.assertRaises(InvalidLifecycleTransitionError):
            record.transition_to(OrderLifecycleState.FILLED)
        self.assertEqual(record.order_status, OrderLifecycleState.PROPOSAL)


if __name__ == "__main__":
    unittest.main()
