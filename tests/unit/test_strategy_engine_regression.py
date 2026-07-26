"""Regression test locking in exact Strategy Engine output for a fixed,
deterministic scenario (same discipline as every prior phase). If this
test ever fails, understand why before updating expected values.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from etf_platform.cost_tax_engine import Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.strategy_engine import (
    AvailableInvestmentPool,
    ContributionSource,
    FundingState,
    RecurringMonthlyPolicy,
    StrategyEngine,
    StrategyStateStore,
)
from etf_platform.strategy_engine.ports import CashLedgerPort, NotificationPort


class FakeCashLedger(CashLedgerPort):
    def __init__(self, balance=0.0):
        self.balance = balance

    def get_available_pool(self, as_of_date):
        return AvailableInvestmentPool(0.0, self.balance, ContributionSource.RECURRING_MONTHLY, as_of_date)

    def get_pending_queue_entries(self):
        return []

    def notify_expected_contribution(self, amount, expected_date, source):
        pass

    def verify_and_finalize(self, proposed_orders):
        return proposed_orders


class FakeNotifier(NotificationPort):
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        return []


def _bars(closes, symbol, start=date(2026, 7, 1)):
    return [
        OHLCVBar(symbol, start + timedelta(days=i), c - 0.2, c + 0.2, c - 0.4, c, 20000)
        for i, c in enumerate(closes)
    ]


class TestStrategyEngineRegressionBaseline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

        closes_a = [100 + (i % 10) * 0.5 for i in range(30)]
        closes_b = [50 + (i % 5) * 0.3 for i in range(30)]
        self.price_history = {"A": _bars(closes_a, "A"), "B": _bars(closes_b, "B")}

        self.strategy = StrategyEngine({"A": 0.6, "B": 0.4})
        self.policy = RecurringMonthlyPolicy(reminder_day=8)
        self.ledger = FakeCashLedger(balance=37500.0)
        self.notifier = FakeNotifier()
        self.store = StrategyStateStore(self.tmp_dir / "state.db")
        self.addCleanup(self.store.close)

    def test_locked_orders_and_final_state(self):
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 0.6, "B": 0.4}, self.price_history,
            self.policy, self.ledger, self.notifier, self.store,
        )

        self.assertEqual(len(result.orders), 2)

        order_a = next(o for o in result.orders if o.symbol == "A")
        self.assertEqual(order_a.quantity, 214)
        self.assertAlmostEqual(order_a.limit_price, 104.81, places=2)
        self.assertEqual(order_a.side, Side.BUY)

        order_b = next(o for o in result.orders if o.symbol == "B")
        self.assertEqual(order_b.quantity, 291)  # cost-aware sizing: 1 unit less than gross-only would allow
        self.assertAlmostEqual(order_b.limit_price, 51.35, places=2)
        self.assertEqual(order_b.side, Side.BUY)

        # Two-phase completion (see CHANGELOG.md): state stays EXECUTING
        # until confirm_cycle_outcome() is explicitly called -- it does NOT
        # advance to IDLE just because orders were computed.
        self.assertEqual(result.funding_state_after, FundingState.EXECUTING)
        self.assertEqual(result.cycle_id, "2026-07-recurring_monthly")

        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, self.store, submitted_successfully=True)
        self.assertEqual(self.store.load().funding_state, FundingState.IDLE)

    def test_deterministic_across_repeated_runs(self):
        result1 = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 0.6, "B": 0.4}, self.price_history,
            self.policy, self.ledger, self.notifier, self.store,
        )
        store2 = StrategyStateStore(self.tmp_dir / "state2.db")
        self.addCleanup(store2.close)
        result2 = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 0.6, "B": 0.4}, self.price_history,
            self.policy, FakeCashLedger(balance=37500.0), FakeNotifier(), store2,
        )
        self.assertEqual(
            [(o.symbol, o.quantity, o.limit_price) for o in result1.orders],
            [(o.symbol, o.quantity, o.limit_price) for o in result2.orders],
        )

    def test_strategy_engine_never_calls_verify_and_finalize_itself(self):
        """CycleResult.orders are proposals only (PHASE1_Architecture_SRS.md
        section 0.1a) -- verify_and_finalize is exclusively the downstream
        orchestrator's responsibility, never Strategy Engine's own."""

        class TrackingCashLedger(FakeCashLedger):
            def __init__(self, balance=0.0):
                super().__init__(balance)
                self.verify_and_finalize_called = False

            def verify_and_finalize(self, proposed_orders):
                self.verify_and_finalize_called = True
                return proposed_orders

        tracking_ledger = TrackingCashLedger(balance=37500.0)
        self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 0.6, "B": 0.4}, self.price_history,
            self.policy, tracking_ledger, self.notifier, self.store,
        )
        self.assertFalse(tracking_ledger.verify_and_finalize_called)


if __name__ == "__main__":
    unittest.main()
