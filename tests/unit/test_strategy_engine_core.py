"""Tests for the StrategyEngine class itself: backtest-interface
compatibility, the shared core allocation logic, and the structural
sell-guard."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.backtesting.models import BacktestConfig, OrderIntent, OrderType, PortfolioSnapshot
from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.cost_tax_engine import Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.strategy_engine import StrategyEngine
from etf_platform.strategy_engine.exceptions import SellInstructionAttemptedError


def bars(closes, symbol="X", start=date(2024, 1, 1)):
    return [
        OHLCVBar(symbol, start + timedelta(days=i), c - 0.3, c + 0.3, c - 0.6, c, 50000)
        for i, c in enumerate(closes)
    ]


class TestBacktestCompatibility(unittest.TestCase):
    def test_runs_through_real_backtest_engine_without_modification(self):
        closes_a = [100 + i * 0.1 for i in range(300)]
        closes_b = [50 + i * 0.05 for i in range(300)]
        price_history = {"A": bars(closes_a, "A"), "B": bars(closes_b, "B")}

        strategy = StrategyEngine({"A": 0.6, "B": 0.4}, deployment_day_of_month=1)
        config = BacktestConfig(
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 1) + timedelta(days=299),
            initial_capital=100000, symbols=("A", "B"),
        )
        engine = BacktestEngine(config, strategy)
        result = engine.run(price_history)

        self.assertGreater(len(result.trades), 0)
        self.assertEqual(result.warnings, [])
        for trade in result.trades:
            self.assertEqual(trade.fill.side, Side.BUY)

    def test_only_deploys_on_configured_day_of_month(self):
        closes = [100 + i * 0.05 for i in range(90)]
        price_history = {"A": bars(closes, "A")}
        strategy = StrategyEngine({"A": 1.0}, deployment_day_of_month=15)
        config = BacktestConfig(
            start_date=date(2024, 1, 1), end_date=date(2024, 3, 30), initial_capital=50000, symbols=("A",),
        )
        engine = BacktestEngine(config, strategy)
        result = engine.run(price_history)
        fill_days = {t.fill.fill_date.day for t in result.trades}
        self.assertTrue(fill_days.issubset({16, 17}))


class TestStrategyEngineDirectly(unittest.TestCase):
    def setUp(self):
        self.strategy = StrategyEngine({"A": 0.6, "B": 0.4})

    def test_no_orders_on_non_deployment_day(self):
        history = {"A": bars([100] * 10, "A"), "B": bars([50] * 10, "B")}
        portfolio = PortfolioSnapshot(as_of_date=date(2024, 1, 5), cash=10000, positions={}, total_value=10000)
        orders = self.strategy.generate_orders(date(2024, 1, 5), history, portfolio)
        self.assertEqual(orders, [])

    def test_no_orders_when_no_cash(self):
        history = {"A": bars([100] * 10, "A"), "B": bars([50] * 10, "B")}
        portfolio = PortfolioSnapshot(as_of_date=date(2024, 1, 1), cash=0, positions={}, total_value=0)
        orders = self.strategy.generate_orders(date(2024, 1, 1), history, portfolio)
        self.assertEqual(orders, [])

    def test_all_orders_are_buy_and_limit(self):
        history = {"A": bars([100] * 10, "A"), "B": bars([50] * 10, "B")}
        portfolio = PortfolioSnapshot(as_of_date=date(2024, 1, 1), cash=100000, positions={}, total_value=100000)
        orders = self.strategy.generate_orders(date(2024, 1, 1), history, portfolio)
        self.assertGreater(len(orders), 0)
        for order in orders:
            self.assertEqual(order.side, Side.BUY)
            self.assertEqual(order.order_type, OrderType.LIMIT)
            self.assertIsNotNone(order.limit_price)

    def test_every_order_has_nonempty_rationale(self):
        history = {"A": bars([100] * 10, "A"), "B": bars([50] * 10, "B")}
        portfolio = PortfolioSnapshot(as_of_date=date(2024, 1, 1), cash=100000, positions={}, total_value=100000)
        orders = self.strategy.generate_orders(date(2024, 1, 1), history, portfolio)
        for order in orders:
            self.assertTrue(order.rationale.strip())


class TestStructuralSellGuard(unittest.TestCase):
    def test_assert_all_buy_only_raises_on_sell(self):
        bad_order = OrderIntent("A", Side.SELL, OrderType.MARKET, 10, "test bad order")
        with self.assertRaises(SellInstructionAttemptedError):
            StrategyEngine._assert_all_buy_only([bad_order])

    def test_assert_all_buy_only_passes_on_buy(self):
        good_order = OrderIntent("A", Side.BUY, OrderType.LIMIT, 10, "test good order", limit_price=100.0)
        StrategyEngine._assert_all_buy_only([good_order])

    def test_normal_operation_never_triggers_the_guard(self):
        history = {"A": bars([100] * 10, "A")}
        portfolio = PortfolioSnapshot(as_of_date=date(2024, 1, 1), cash=50000, positions={}, total_value=50000)
        strategy = StrategyEngine({"A": 1.0})
        orders = strategy.generate_orders(date(2024, 1, 1), history, portfolio)
        self.assertTrue(all(o.side == Side.BUY for o in orders))


if __name__ == "__main__":
    unittest.main()
