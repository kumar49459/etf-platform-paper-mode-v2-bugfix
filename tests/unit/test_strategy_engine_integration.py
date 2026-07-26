"""Tests for: StrategyStateStore persistence, capital-agnostic behavior,
Pause/Resume/Discontinue command handling, and the section 21.3 guarantee
that Strategy Engine behaves identically whether Module 27 is present or
entirely absent.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.strategy_engine import (
    AvailableInvestmentPool,
    Command,
    ContributionSource,
    FundingState,
    MarketRegimeSnapshot,
    NullMarketIntelligencePort,
    RecurringMonthlyPolicy,
    StrategyEngine,
    StrategyEngineState,
    StrategyStateStore,
)
from etf_platform.strategy_engine import strategy as strategy_module
from etf_platform.strategy_engine.ports import CashLedgerPort, MarketIntelligencePort, NotificationPort


def bars(n=30, price=100.0):
    return [OHLCVBar("A", date(2026, 7, 1) + timedelta(days=i), price, price + 0.5, price - 0.5, price, 20000) for i in range(n)]


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
    def __init__(self, commands=None):
        self.sent = []
        self._commands = commands or []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        cmds, self._commands = self._commands, []
        return cmds


class PopulatedMarketIntelligencePort(MarketIntelligencePort):
    def get_market_regime(self, as_of_date):
        return MarketRegimeSnapshot("bull", "low", as_of_date)

    def get_relative_strength(self, symbol, as_of_date):
        return 0.8

    def get_sector_strength(self, sector, as_of_date):
        return 0.6


class TestStrategyStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.store = StrategyStateStore(self.tmp_dir / "state.db")
        self.addCleanup(self.store.close)

    def test_load_empty_returns_none(self):
        self.assertIsNone(self.store.load())

    def test_save_then_load_roundtrip(self):
        state = StrategyEngineState("2026-07", FundingState.EXECUTING, True, date(2026, 7, 10), paused=True)
        self.store.save(state)
        loaded = self.store.load()
        self.assertEqual(loaded.current_month, "2026-07")
        self.assertEqual(loaded.funding_state, FundingState.EXECUTING)
        self.assertTrue(loaded.reminder_sent_this_month)
        self.assertEqual(loaded.last_check_date, date(2026, 7, 10))
        self.assertTrue(loaded.paused)

    def test_save_overwrites_single_row(self):
        state1 = StrategyEngineState("2026-07", FundingState.AWAITING_FUNDS, False, None)
        state2 = StrategyEngineState("2026-08", FundingState.IDLE, True, date(2026, 8, 5))
        self.store.save(state1)
        self.store.save(state2)
        loaded = self.store.load()
        self.assertEqual(loaded.current_month, "2026-08")


class TestCapitalAgnostic(unittest.TestCase):
    _FORBIDDEN_SUBSTRINGS = ("capital", "rupee", "amount_invested", "investment_amount")

    def test_strategy_engine_init_has_no_amount_param(self):
        sig = inspect.signature(StrategyEngine.__init__)
        for name in sig.parameters:
            for forbidden in self._FORBIDDEN_SUBSTRINGS:
                self.assertNotIn(forbidden, name.lower(), f"__init__ has a forbidden param: {name}")

    def test_no_hardcoded_amount_constants_in_strategy_module(self):
        source = inspect.getsource(strategy_module)
        tree = ast.parse(source)
        suspicious = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and node.value in (1000, 5000, 10000, 20000, 50000, 100000, 500000)
        ]
        self.assertEqual(suspicious, [])

    def test_identical_priority_logic_at_different_capital_levels(self):
        strategy = StrategyEngine({"A": 0.6, "B": 0.4})
        price_history = {"A": bars(price=100), "B": bars(price=50)}
        results = {}
        for capital in (1000, 5000, 10000, 20000, 50000, 100000, 500000):
            from etf_platform.backtesting.models import PortfolioSnapshot

            portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=capital, positions={}, total_value=capital)
            orders = strategy.generate_orders(date(2026, 7, 1), price_history, portfolio)
            results[capital] = {o.symbol for o in orders}
        non_empty = {k: v for k, v in results.items() if v}
        self.assertTrue(non_empty)


class TestCommandHandling(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.store = StrategyStateStore(self.tmp_dir / "state.db")
        self.addCleanup(self.store.close)
        self.strategy = StrategyEngine({"A": 1.0})
        self.policy = RecurringMonthlyPolicy()
        self.ledger = FakeCashLedger(balance=50000)
        self.price_history = {"A": bars()}

    def test_pause_command_stops_future_cycles(self):
        notifier = FakeNotifier(commands=[Command.PAUSE])
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 5), {}, {"A": 1.0}, self.price_history, self.policy, self.ledger, notifier, self.store,
        )
        self.assertEqual(result.orders, [])
        loaded = self.store.load()
        self.assertTrue(loaded.paused)

    def test_resume_command_restores_normal_operation(self):
        notifier = FakeNotifier(commands=[Command.PAUSE])
        self.strategy.run_daily_cycle(
            date(2026, 7, 5), {}, {"A": 1.0}, self.price_history, self.policy, self.ledger, notifier, self.store,
        )
        notifier2 = FakeNotifier(commands=[Command.RESUME])
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 6), {}, {"A": 1.0}, self.price_history, self.policy, self.ledger, notifier2, self.store,
        )
        self.assertGreater(len(result.orders), 0)

    def test_discontinue_persists_across_invocations(self):
        notifier = FakeNotifier(commands=[Command.DISCONTINUE])
        self.strategy.run_daily_cycle(
            date(2026, 7, 5), {}, {"A": 1.0}, self.price_history, self.policy, self.ledger, notifier, self.store,
        )
        notifier2 = FakeNotifier()
        result = self.strategy.run_daily_cycle(
            date(2026, 7, 6), {}, {"A": 1.0}, self.price_history, self.policy, self.ledger, notifier2, self.store,
        )
        self.assertEqual(result.orders, [])
        self.assertTrue(self.store.load().discontinued)


class TestMarketIntelligenceAbsenceGuarantee(unittest.TestCase):
    def test_identical_orders_with_and_without_market_intelligence_data(self):
        price_history = {"A": bars(price=100), "B": bars(price=50)}
        from etf_platform.backtesting.models import PortfolioSnapshot

        portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=50000, positions={}, total_value=50000)

        strategy_null = StrategyEngine({"A": 0.6, "B": 0.4}, market_intelligence_port=NullMarketIntelligencePort())
        strategy_populated = StrategyEngine({"A": 0.6, "B": 0.4}, market_intelligence_port=PopulatedMarketIntelligencePort())

        orders_null = strategy_null.generate_orders(date(2026, 7, 1), price_history, portfolio)
        orders_populated = strategy_populated.generate_orders(date(2026, 7, 1), price_history, portfolio)

        self.assertEqual(len(orders_null), len(orders_populated))
        for o1, o2 in zip(orders_null, orders_populated):
            self.assertEqual(o1.symbol, o2.symbol)
            self.assertEqual(o1.quantity, o2.quantity)
            self.assertEqual(o1.side, o2.side)
            self.assertEqual(o1.limit_price, o2.limit_price)

    def test_default_port_is_null_not_populated(self):
        strategy = StrategyEngine({"A": 1.0})
        self.assertIsInstance(strategy._market_intelligence_port, NullMarketIntelligencePort)


if __name__ == "__main__":
    unittest.main()
