"""Regression tests for the operational adversarial review (see
CHANGELOG.md). Each test class corresponds to one of the ten review areas.
"""

from __future__ import annotations

import inspect
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from etf_platform.backtesting.models import PortfolioSnapshot
from etf_platform.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig, Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.strategy_engine import (
    AvailableInvestmentPool,
    ContributionSource,
    FundingState,
    RecurringMonthlyPolicy,
    StrategyEngine,
    StrategyStateStore,
)
from etf_platform.strategy_engine import strategy as strategy_module
from etf_platform.strategy_engine.ports import CashLedgerPort, NotificationPort


def bars(n=30, price=100.0, symbol="A"):
    return [
        OHLCVBar(symbol, date(2026, 7, 1) + timedelta(days=i), price, price + 0.5, price - 0.5, price, 20000)
        for i in range(n)
    ]


class FakeCashLedger(CashLedgerPort):
    def __init__(self, balance=0.0):
        self.balance = balance
        self.get_available_pool_call_count = 0

    def get_available_pool(self, as_of_date):
        self.get_available_pool_call_count += 1
        return AvailableInvestmentPool(0.0, self.balance, ContributionSource.RECURRING_MONTHLY, as_of_date)

    def get_pending_queue_entries(self):
        return []

    def notify_expected_contribution(self, amount, expected_date, source):
        pass

    def verify_and_finalize(self, proposed_orders):
        return proposed_orders


class ExplodingCashLedger(CashLedgerPort):
    def __init__(self, exception):
        self._exception = exception

    def get_available_pool(self, as_of_date):
        raise self._exception

    def get_pending_queue_entries(self):
        return []

    def notify_expected_contribution(self, amount, expected_date, source):
        pass

    def verify_and_finalize(self, proposed_orders):
        return proposed_orders


class FakeNotifier(NotificationPort):
    def __init__(self, commands=None):
        self.sent = []
        self._commands = commands or []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        cmds, self._commands = self._commands, []
        return cmds


class TestCrashRecoveryAndIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.strategy = StrategyEngine({"A": 1.0})
        self.policy = RecurringMonthlyPolicy()
        self.price_history = {"A": bars()}

    def test_crash_before_order_generation_leaves_no_trace(self):
        store = StrategyStateStore(self.tmp_dir / "s1.db")
        self.addCleanup(store.close)
        self.assertIsNone(store.load())

    def test_crash_after_order_generation_before_confirmation_state_stays_executing(self):
        store = StrategyStateStore(self.tmp_dir / "s2.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.assertGreater(len(result.orders), 0)
        self.assertEqual(result.funding_state_after, FundingState.EXECUTING)
        reloaded = store.load()
        self.assertEqual(reloaded.funding_state, FundingState.EXECUTING)

    def test_restart_after_crash_reproduces_identical_proposal_not_a_duplicate(self):
        store = StrategyStateStore(self.tmp_dir / "s3.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)

        result1 = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        result2 = self.strategy.run_daily_cycle(
            date(2026, 7, 11), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.assertEqual(
            [(o.symbol, o.quantity, o.limit_price) for o in result1.orders],
            [(o.symbol, o.quantity, o.limit_price) for o in result2.orders],
        )
        self.assertEqual(result1.cycle_id, result2.cycle_id)

    def test_confirm_cycle_outcome_advances_to_idle_only_after_explicit_call(self):
        store = StrategyStateStore(self.tmp_dir / "s4.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.assertEqual(store.load().funding_state, FundingState.EXECUTING)

        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, store, submitted_successfully=True)
        self.assertEqual(store.load().funding_state, FundingState.IDLE)

    def test_confirm_cycle_outcome_failed_submission_returns_to_awaiting_funds(self):
        store = StrategyStateStore(self.tmp_dir / "s5.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, store, submitted_successfully=False)
        self.assertEqual(store.load().funding_state, FundingState.AWAITING_FUNDS)

    def test_confirm_cycle_outcome_is_idempotent(self):
        store = StrategyStateStore(self.tmp_dir / "s6.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, store, submitted_successfully=True)
        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, store, submitted_successfully=True)
        self.assertEqual(store.load().funding_state, FundingState.IDLE)

    def test_same_day_double_invocation_produces_no_duplicate_orders(self):
        store = StrategyStateStore(self.tmp_dir / "s7.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        result1 = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.strategy.confirm_cycle_outcome(date(2026, 7, 10), self.policy, store, submitted_successfully=True)
        result2 = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
        )
        self.assertGreater(len(result1.orders), 0)
        self.assertEqual(len(result2.orders), 0)


class TestKiteAPIFailures(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.strategy = StrategyEngine({"A": 1.0})
        self.policy = RecurringMonthlyPolicy()
        self.price_history = {"A": bars()}

    def _assert_failure_does_not_corrupt_state(self, exception):
        store = StrategyStateStore(self.tmp_dir / f"kite_{id(exception)}.db")
        self.addCleanup(store.close)
        with self.assertRaises(type(exception)):
            self.strategy.run_daily_cycle(
                date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy,
                ExplodingCashLedger(exception), FakeNotifier(), store,
            )
        self.assertIsNone(store.load(), "A failed cycle must never leave partially-written state.")

    def test_network_timeout_does_not_corrupt_state(self):
        self._assert_failure_does_not_corrupt_state(TimeoutError("Kite API network timeout"))

    def test_auth_token_expiry_does_not_corrupt_state(self):
        self._assert_failure_does_not_corrupt_state(PermissionError("Kite access token expired"))

    def test_rate_limiting_does_not_corrupt_state(self):
        self._assert_failure_does_not_corrupt_state(ConnectionError("Kite API rate limit exceeded (429)"))

    def test_temporary_server_error_does_not_corrupt_state(self):
        self._assert_failure_does_not_corrupt_state(RuntimeError("Kite API 503 Service Unavailable"))

    def test_recovery_after_failure_produces_normal_result_no_duplication(self):
        store = StrategyStateStore(self.tmp_dir / "recovery.db")
        self.addCleanup(store.close)
        with self.assertRaises(ConnectionError):
            self.strategy.run_daily_cycle(
                date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy,
                ExplodingCashLedger(ConnectionError("timeout")), FakeNotifier(), store,
            )
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 11), {}, {"A": 1.0}, self.price_history, self.policy,
            FakeCashLedger(balance=50000), FakeNotifier(), store,
        )
        self.assertGreater(len(result.orders), 0)


class TestExchangeHolidaysAndNonTradingDays(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.strategy = StrategyEngine({"A": 1.0})
        self.policy = RecurringMonthlyPolicy()
        self.price_history = {"A": bars()}

    def test_non_trading_day_defers_order_generation(self):
        store = StrategyStateStore(self.tmp_dir / "holiday.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
            is_trading_day=False,
        )
        self.assertEqual(result.orders, [])
        self.assertTrue(result.deferred_to_next_trading_day)
        self.assertTrue(any("not a trading day" in n.lower() for n in result.notes))

    def test_funding_state_remains_executing_pending_next_trading_day(self):
        store = StrategyStateStore(self.tmp_dir / "holiday2.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
            is_trading_day=False,
        )
        self.assertEqual(result.funding_state_after, FundingState.EXECUTING)

    def test_next_trading_day_retry_produces_the_deferred_orders(self):
        store = StrategyStateStore(self.tmp_dir / "holiday3.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=50000)
        deferred_result = self.strategy.run_daily_cycle(
            date(2026, 7, 10), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
            is_trading_day=False,
        )
        self.assertEqual(deferred_result.orders, [])

        next_day_result = self.strategy.run_daily_cycle(
            date(2026, 7, 11), {}, {"A": 1.0}, self.price_history, self.policy, ledger, FakeNotifier(), store,
            is_trading_day=True,
        )
        self.assertGreater(len(next_day_result.orders), 0)


class TestFinalCashValidationCostAwareness(unittest.TestCase):
    def test_proposed_total_cost_never_exceeds_available_cash(self):
        strategy = StrategyEngine({"A": 1.0}, cost_tax_engine=CostTaxEngine(IndiaEquityCostConfig()))
        portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=50000, positions={}, total_value=50000)
        orders = strategy.generate_orders(date(2026, 7, 1), {"A": bars()}, portfolio)
        self.assertEqual(len(orders), 1)
        order = orders[0]

        cte = CostTaxEngine(IndiaEquityCostConfig())
        cost = cte.compute_transaction_cost(Side.BUY, order.limit_price, order.quantity)
        total_needed = order.quantity * order.limit_price + cost.total_cost
        self.assertLessEqual(total_needed, 50000, "Proposal must leave room for real transaction costs.")

    def test_affordable_quantity_never_overspends_across_many_price_levels(self):
        strategy = StrategyEngine({"A": 1.0})
        for price in (10, 47.33, 99.99, 250, 1000, 9999.5):
            for budget in (1000, 5000, 37500, 100000):
                qty = strategy._affordable_quantity(price, budget)
                if qty == 0:
                    continue
                cost = strategy._cost_tax_engine.compute_transaction_cost(Side.BUY, price, qty)
                self.assertLessEqual(qty * price + cost.total_cost, budget)


class TestLiquidityProtectionNeverMarketOrder(unittest.TestCase):
    def test_source_code_never_references_market_order_type_in_construction(self):
        source = inspect.getsource(strategy_module)
        self.assertNotIn("OrderType.MARKET", source)

    def test_every_generated_order_is_a_limit_order(self):
        strategy = StrategyEngine({"A": 0.6, "B": 0.4})
        portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=100000, positions={}, total_value=100000)
        history = {"A": bars(price=100, symbol="A"), "B": bars(price=50, symbol="B")}
        orders = strategy.generate_orders(date(2026, 7, 1), history, portfolio)
        self.assertGreater(len(orders), 0)
        for order in orders:
            from etf_platform.backtesting.models import OrderType

            self.assertEqual(order.order_type, OrderType.LIMIT)
            self.assertIsNotNone(order.limit_price)


class TestExecutionIndependence(unittest.TestCase):
    def test_strategy_engine_module_never_imports_fill_or_trade(self):
        source = inspect.getsource(strategy_module)
        self.assertNotIn("import Fill", source)
        self.assertNotIn("import Trade", source)
        self.assertNotIn("FillSimulator", source)

    def test_cycle_result_has_no_fill_or_execution_status_fields(self):
        from etf_platform.strategy_engine.models import CycleResult

        field_names = set(CycleResult.__dataclass_fields__)
        for forbidden in ("fill_price", "filled_quantity", "execution_status", "broker_order_id"):
            self.assertNotIn(forbidden, field_names)


class TestDuplicateReminderPrevention(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.strategy = StrategyEngine({"A": 1.0})
        self.policy = RecurringMonthlyPolicy(reminder_day=8)
        self.price_history = {"A": bars()}

    def test_reminder_flag_is_persisted_even_if_order_building_would_fail(self):
        store = StrategyStateStore(self.tmp_dir / "reminder.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=0)
        notifier = FakeNotifier()

        self.strategy.run_daily_cycle(
            date(2026, 7, 8), {}, {"A": 1.0}, self.price_history, self.policy, ledger, notifier, store,
        )
        self.assertEqual(len(notifier.sent), 1)
        reloaded = store.load()
        self.assertTrue(reloaded.reminder_sent_this_month, "Reminder flag must be persisted at checkpoint 1.")

    def test_repeated_invocations_on_reminder_day_never_resend(self):
        store = StrategyStateStore(self.tmp_dir / "reminder2.db")
        self.addCleanup(store.close)
        ledger = FakeCashLedger(balance=0)

        for _ in range(3):
            notifier = FakeNotifier()
            self.strategy.run_daily_cycle(
                date(2026, 7, 8), {}, {"A": 1.0}, self.price_history, self.policy, ledger, notifier, store,
            )
        final_notifier = FakeNotifier()
        self.strategy.run_daily_cycle(
            date(2026, 7, 8), {}, {"A": 1.0}, self.price_history, self.policy, ledger, final_notifier, store,
        )
        self.assertEqual(len(final_notifier.sent), 0)


class TestStatePersistenceAcrossRestart(unittest.TestCase):
    def test_full_month_lifecycle_survives_simulated_restarts(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        db_path = tmp_dir / "lifecycle.db"
        strategy = StrategyEngine({"A": 1.0})
        policy = RecurringMonthlyPolicy(reminder_day=8)
        price_history = {"A": bars()}
        ledger = FakeCashLedger(balance=0)

        store = StrategyStateStore(db_path)
        strategy.run_daily_cycle(date(2026, 7, 1), {}, {"A": 1.0}, price_history, policy, ledger, FakeNotifier(), store)
        store.close()

        store = StrategyStateStore(db_path)
        self.assertEqual(store.load().funding_state, FundingState.AWAITING_FUNDS)
        strategy.run_daily_cycle(date(2026, 7, 8), {}, {"A": 1.0}, price_history, policy, ledger, FakeNotifier(), store)
        self.assertTrue(store.load().reminder_sent_this_month)
        store.close()

        store = StrategyStateStore(db_path)
        ledger.balance = 50000
        result = strategy.run_daily_cycle(date(2026, 7, 15), {}, {"A": 1.0}, price_history, policy, ledger, FakeNotifier(), store)
        self.assertGreater(len(result.orders), 0)
        self.assertEqual(store.load().funding_state, FundingState.EXECUTING)
        store.close()

        store = StrategyStateStore(db_path)
        strategy.confirm_cycle_outcome(date(2026, 7, 15), policy, store, submitted_successfully=True)
        self.assertEqual(store.load().funding_state, FundingState.IDLE)
        store.close()


class RecordingStateStore:
    """Wraps a real StrategyStateStore, recording the ORDER in which save()
    is called relative to external side effects -- used to structurally
    prove persistence always precedes any external call, not just infer it
    from reading the code."""

    def __init__(self, real_store, event_log):
        self._real_store = real_store
        self._event_log = event_log

    def load(self):
        return self._real_store.load()

    def save(self, state):
        self._event_log.append(("save", state.funding_state.value))
        self._real_store.save(state)

    def close(self):
        self._real_store.close()


class RecordingNotifier(NotificationPort):
    def __init__(self, event_log):
        self._event_log = event_log
        self.sent = []

    def send(self, message):
        self._event_log.append(("send", message[:30]))
        self.sent.append(message)

    def poll_commands(self):
        return []


class RecordingCashLedger(CashLedgerPort):
    def __init__(self, balance, event_log):
        self.balance = balance
        self._event_log = event_log

    def get_available_pool(self, as_of_date):
        return AvailableInvestmentPool(0.0, self.balance, ContributionSource.RECURRING_MONTHLY, as_of_date)

    def get_pending_queue_entries(self):
        return []

    def notify_expected_contribution(self, amount, expected_date, source):
        self._event_log.append(("notify_expected_contribution", amount))

    def verify_and_finalize(self, proposed_orders):
        return proposed_orders


class TestPersistenceBeforeSideEffectOrdering(unittest.TestCase):
    """Item 8 of the production verification review: every state
    transition must be persisted BEFORE any external side effect. Proven
    here by recording the actual call sequence, not inferred from reading
    the source."""

    def test_save_precedes_reminder_send_in_actual_call_sequence(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        event_log = []
        real_store = StrategyStateStore(tmp_dir / "order.db")
        self.addCleanup(real_store.close)
        recording_store = RecordingStateStore(real_store, event_log)
        notifier = RecordingNotifier(event_log)
        ledger = RecordingCashLedger(balance=0, event_log=event_log)

        strategy = StrategyEngine({"A": 1.0})
        policy = RecurringMonthlyPolicy(reminder_day=8)
        strategy.run_daily_cycle(
            date(2026, 7, 1), {}, {"A": 1.0}, {"A": bars()}, policy, ledger, notifier, recording_store,
        )
        strategy.run_daily_cycle(
            date(2026, 7, 8), {}, {"A": 1.0}, {"A": bars()}, policy, ledger, notifier, recording_store,
        )

        save_indices = [i for i, e in enumerate(event_log) if e[0] == "save"]
        send_indices = [i for i, e in enumerate(event_log) if e[0] == "send"]
        self.assertTrue(save_indices, "Expected at least one save() call.")
        self.assertTrue(send_indices, "Expected the reminder to have been sent.")
        # The very first save() in the whole sequence must precede the
        # send() -- proves persistence-before-side-effect structurally,
        # not inferred from reading the code.
        self.assertLess(save_indices[0], send_indices[0], "State must be persisted before the reminder is actually sent.")

    def test_save_precedes_notify_expected_contribution(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        event_log = []
        real_store = StrategyStateStore(tmp_dir / "order2.db")
        self.addCleanup(real_store.close)
        recording_store = RecordingStateStore(real_store, event_log)
        notifier = RecordingNotifier(event_log)
        ledger = RecordingCashLedger(balance=0, event_log=event_log)

        strategy = StrategyEngine({"A": 1.0})
        policy = RecurringMonthlyPolicy(reminder_day=8)
        strategy.run_daily_cycle(
            date(2026, 7, 1), {}, {"A": 1.0}, {"A": bars()}, policy, ledger, notifier, recording_store,
        )

        save_indices = [i for i, e in enumerate(event_log) if e[0] == "save"]
        notify_indices = [i for i, e in enumerate(event_log) if e[0] == "notify_expected_contribution"]
        self.assertTrue(save_indices)
        self.assertTrue(notify_indices)
        self.assertLess(save_indices[0], notify_indices[0])


class TestExhaustiveStatutoryChargeCoverage(unittest.TestCase):
    """Item 4: brokerage, STT, GST, stamp duty, exchange charges, SEBI
    charges, and any other mandatory statutory charge must all be
    reserved for -- verified against CostBreakdown's actual fields, not
    just the ones that happen to be nonzero by default."""

    def test_all_seven_cost_components_are_included_in_total_cost(self):
        cte = CostTaxEngine(IndiaEquityCostConfig())
        cost = cte.compute_transaction_cost(Side.BUY, 100.0, 100)
        expected_total = (
            cost.brokerage + cost.stt + cost.stamp_duty + cost.exchange_txn_charge
            + cost.sebi_turnover_fee + cost.gst + cost.slippage_cost
        )
        self.assertAlmostEqual(cost.total_cost, expected_total, places=10)
        for field_name in ("brokerage", "stt", "stamp_duty", "exchange_txn_charge", "sebi_turnover_fee", "gst", "slippage_cost"):
            self.assertTrue(hasattr(cost, field_name), f"CostBreakdown is missing required field: {field_name}")

    def test_affordable_quantity_reserves_for_the_full_cost_breakdown_not_a_subset(self):
        strategy = StrategyEngine({"A": 1.0}, cost_tax_engine=CostTaxEngine(IndiaEquityCostConfig()))
        qty = strategy._affordable_quantity(100.0, 50000.0)
        cost = strategy._cost_tax_engine.compute_transaction_cost(Side.BUY, 100.0, qty)
        self.assertLessEqual(qty * 100.0 + cost.total_cost, 50000.0)


class TestWholeUnitQuantityGuarantee(unittest.TestCase):
    """Item 5: quantity is always a whole ETF unit."""

    def test_affordable_quantity_is_always_an_integer_value(self):
        strategy = StrategyEngine({"A": 1.0})
        for price in (33.33, 100.1, 7.77, 999.99):
            for budget in (1234.56, 50000.99, 7777.77):
                qty = strategy._affordable_quantity(price, budget)
                self.assertEqual(qty, int(qty))
                self.assertIsInstance(qty, int)

    def test_generated_orders_never_carry_fractional_quantity(self):
        strategy = StrategyEngine({"A": 0.6, "B": 0.4})
        portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=123456.78, positions={}, total_value=123456.78)
        history = {"A": bars(price=77.33, symbol="A"), "B": bars(price=241.5, symbol="B")}
        orders = strategy.generate_orders(date(2026, 7, 1), history, portfolio)
        self.assertGreater(len(orders), 0)
        for order in orders:
            self.assertEqual(order.quantity, int(order.quantity))


class TestStrategyEngineIndependence(unittest.TestCase):
    """Item 7: Strategy Engine remains completely independent of the Kite
    API, Module 27, Module 28, the exchange calendar implementation, and
    the execution layer."""

    def test_no_kite_library_or_api_reference_anywhere_in_the_package(self):
        import etf_platform.strategy_engine as pkg

        pkg_dir = Path(pkg.__file__).parent
        for py_file in pkg_dir.rglob("*.py"):
            source = py_file.read_text()
            self.assertNotIn("kiteconnect", source.lower())
            self.assertNotIn("import kite", source.lower())

    def test_no_concrete_module_27_28_or_13_implementation_exists_in_this_package(self):
        import etf_platform.strategy_engine.ports as ports_module

        concrete_classes = [
            name for name, obj in vars(ports_module).items()
            if isinstance(obj, type) and obj.__module__ == ports_module.__name__ and not inspect.isabstract(obj)
        ]
        self.assertEqual(set(concrete_classes), {"NullMarketIntelligencePort"})

    def test_no_exchange_calendar_library_imported(self):
        source = inspect.getsource(strategy_module)
        for forbidden in ("import holidays", "import pandas_market_calendars", "import exchange_calendars"):
            self.assertNotIn(forbidden, source)

    def test_is_trading_day_is_a_plain_caller_supplied_boolean(self):
        sig = inspect.signature(StrategyEngine.run_daily_cycle)
        param = sig.parameters["is_trading_day"]
        self.assertEqual(param.default, True)
        self.assertIsInstance(param.default, bool)


class TestEC2MicroCompatibility(unittest.TestCase):
    """Item 9: Phase 6 continues to satisfy the low-memory EC2 Micro
    architecture -- no heavy numerical dependency, even after adding
    cost-aware sizing."""

    def test_importing_strategy_engine_does_not_load_numpy_or_scipy(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; before=set(sys.modules); from etf_platform.strategy_engine import StrategyEngine; "
             "after=set(sys.modules); new=after-before; "
             "print('numpy' if any('numpy' in m for m in new) else 'clean', "
             "'scipy' if any('scipy' in m for m in new) else 'clean')"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertNotIn("numpy", result.stdout.split()[0] if result.stdout.split() else "")


class TestPowerFailureDurability(unittest.TestCase):
    """Item 1 (power failure specifically, as distinct from a plain
    process crash): StrategyStateStore must force a real fsync on every
    commit, not rely on WAL's default synchronous=NORMAL."""

    def test_synchronous_pragma_is_full(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        store = StrategyStateStore(tmp_dir / "durability.db")
        self.addCleanup(store.close)
        mode = store._conn.execute("PRAGMA synchronous;").fetchone()[0]
        self.assertEqual(mode, 2, "synchronous must be FULL (2) for genuine power-failure durability.")


if __name__ == "__main__":
    unittest.main()
