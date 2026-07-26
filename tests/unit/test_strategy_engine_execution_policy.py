"""Exhaustive tests for RecurringMonthlyPolicy's state machine
(PHASE6_Objectives.md section 3.2) and LumpSumPolicy.

Updated for the production-verification-review interface change: run_cycle
now returns (state, pool, notes, pending_side_effects) instead of directly
calling notification_port.send()/cash_ledger_port.notify_expected_contribution()
-- see execution_policy/base.py's docstring and CHANGELOG.md.
"""

from __future__ import annotations

import unittest
from datetime import date

from etf_platform.strategy_engine.execution_policy import LumpSumPolicy, RecurringMonthlyPolicy
from etf_platform.strategy_engine.models import AvailableInvestmentPool, ContributionSource, FundingState
from etf_platform.strategy_engine.ports import CashLedgerPort, NotificationPort


class FakeCashLedger(CashLedgerPort):
    def __init__(self, balance=0.0):
        self.balance = balance
        self.notified = []

    def get_available_pool(self, as_of_date):
        return AvailableInvestmentPool(0.0, self.balance, ContributionSource.RECURRING_MONTHLY, as_of_date)

    def get_pending_queue_entries(self):
        return []

    def notify_expected_contribution(self, amount, expected_date, source):
        self.notified.append((amount, expected_date, source))

    def verify_and_finalize(self, proposed_orders):
        return proposed_orders


class FakeNotifier(NotificationPort):
    def __init__(self):
        self.sent = []
        self.commands = []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        cmds, self.commands = self.commands, []
        return cmds


class TestRecurringMonthlyStateMachine(unittest.TestCase):
    def setUp(self):
        self.policy = RecurringMonthlyPolicy(reminder_day=8)
        self.ledger = FakeCashLedger()
        self.notifier = FakeNotifier()

    def test_first_call_resets_to_awaiting_funds_and_returns_pending_contribution(self):
        state, pool, notes, effects = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.assertEqual(state.funding_state, FundingState.AWAITING_FUNDS)
        self.assertIsNone(pool)
        self.assertIsNotNone(effects.expected_contribution)
        # Policy itself must NOT have called the port directly.
        self.assertEqual(len(self.ledger.notified), 0)

    def test_no_funds_produces_no_pool_and_no_reminder_before_day_8(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state, pool, notes, effects = self.policy.run_cycle(date(2026, 7, 5), state, self.ledger, self.notifier)
        self.assertIsNone(pool)
        self.assertIsNone(effects.reminder_message)

    def test_reminder_pending_exactly_once_at_day_8(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state, _, notes, effects = self.policy.run_cycle(date(2026, 7, 8), state, self.ledger, self.notifier)
        self.assertIsNotNone(effects.reminder_message)
        self.assertTrue(state.reminder_sent_this_month)
        state, _, _, effects2 = self.policy.run_cycle(date(2026, 7, 9), state, self.ledger, self.notifier)
        state, _, _, effects3 = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        self.assertIsNone(effects2.reminder_message)
        self.assertIsNone(effects3.reminder_message)

    def test_reminder_message_is_explicitly_not_a_cancellation(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        _, _, _, effects = self.policy.run_cycle(date(2026, 7, 8), state, self.ledger, self.notifier)
        self.assertIn("not a cancellation", effects.reminder_message.lower())

    def test_policy_never_calls_notification_port_directly(self):
        """The core fix: run_cycle must be side-effect-free with respect to
        notification_port and cash_ledger_port writes -- it only ever
        returns pending decisions."""
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.policy.run_cycle(date(2026, 7, 8), state, self.ledger, self.notifier)
        self.assertEqual(self.notifier.sent, [], "Policy must never call notification_port.send() itself.")
        self.assertEqual(self.ledger.notified, [], "Policy must never call notify_expected_contribution() itself.")

    def test_funds_detected_transitions_to_executing_and_returns_pool(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.ledger.balance = 20000
        state, pool, notes, _ = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        self.assertEqual(state.funding_state, FundingState.EXECUTING)
        self.assertIsNotNone(pool)
        self.assertEqual(pool.new_capital, 20000)

    def test_mark_complete_with_orders_transitions_to_idle(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state = self.policy.mark_cycle_complete(state, any_orders_produced=True)
        self.assertEqual(state.funding_state, FundingState.IDLE)

    def test_mark_complete_without_orders_stays_awaiting(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state = self.policy.mark_cycle_complete(state, any_orders_produced=False)
        self.assertEqual(state.funding_state, FundingState.AWAITING_FUNDS)

    def test_idle_state_performs_no_further_checks(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.ledger.balance = 20000
        state, pool, _, _ = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        state = self.policy.mark_cycle_complete(state, any_orders_produced=True)
        state, pool2, notes, _ = self.policy.run_cycle(date(2026, 7, 11), state, self.ledger, self.notifier)
        self.assertIsNone(pool2)
        self.assertEqual(state.funding_state, FundingState.IDLE)

    def test_new_month_resets_from_idle_to_awaiting_funds(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.ledger.balance = 20000
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        state = self.policy.mark_cycle_complete(state, any_orders_produced=True)
        self.assertEqual(state.funding_state, FundingState.IDLE)

        state, pool, notes, _ = self.policy.run_cycle(date(2026, 8, 1), state, self.ledger, self.notifier)
        self.assertEqual(state.current_month, "2026-08")
        self.assertTrue(any("New month detected" in n for n in notes))

    def test_new_month_resets_reminder_flag(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 8), state, self.ledger, self.notifier)
        self.assertTrue(state.reminder_sent_this_month)
        state, _, _, _ = self.policy.run_cycle(date(2026, 8, 1), state, self.ledger, self.notifier)
        self.assertFalse(state.reminder_sent_this_month)

    def test_paused_state_skips_funding_check_entirely(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state.paused = True
        self.ledger.balance = 20000
        state, pool, notes, _ = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        self.assertIsNone(pool)
        self.assertTrue(any("PAUSED" in n for n in notes))

    def test_discontinued_state_skips_funding_check_entirely(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        state.discontinued = True
        self.ledger.balance = 20000
        state, pool, notes, _ = self.policy.run_cycle(date(2026, 7, 10), state, self.ledger, self.notifier)
        self.assertIsNone(pool)
        self.assertTrue(any("DISCONTINUED" in n for n in notes))

    def test_reminder_day_boundary_validation(self):
        with self.assertRaises(ValueError):
            RecurringMonthlyPolicy(reminder_day=0)
        with self.assertRaises(ValueError):
            RecurringMonthlyPolicy(reminder_day=29)

    def test_partial_funds_used_as_is_not_blocked_pending_more(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.ledger.balance = 500
        state, pool, _, _ = self.policy.run_cycle(date(2026, 7, 5), state, self.ledger, self.notifier)
        self.assertIsNotNone(pool)
        self.assertEqual(pool.new_capital, 500)


class TestLumpSumPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = LumpSumPolicy()
        self.ledger = FakeCashLedger()
        self.notifier = FakeNotifier()

    def test_no_reminder_milestone_ever_pending(self):
        state, _, _, effects = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.assertIsNone(effects.reminder_message)
        for day in range(1, 29):
            state, _, _, effects = self.policy.run_cycle(date(2026, 7, day), state, self.ledger, self.notifier)
            self.assertIsNone(effects.reminder_message)

    def test_funds_detected_and_deployed_same_as_recurring(self):
        state, _, _, _ = self.policy.run_cycle(date(2026, 7, 1), None, self.ledger, self.notifier)
        self.ledger.balance = 300000
        state, pool, _, _ = self.policy.run_cycle(date(2026, 7, 2), state, self.ledger, self.notifier)
        self.assertIsNotNone(pool)
        self.assertEqual(pool.new_capital, 300000)


if __name__ == "__main__":
    unittest.main()
