"""Tests for ai_allocation - proving each of the 7 requirements concretely,
not just asserting the interface exists in the right shape."""

from __future__ import annotations

import inspect
import unittest
from datetime import date, timedelta

from etf_platform.ai_allocation import AIAllocationPort, DisabledAIAllocationPort
from etf_platform.backtesting.models import PortfolioSnapshot
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.strategy_engine import StrategyEngine


def bars(price, symbol, n=10):
    return [
        OHLCVBar(symbol, date(2026, 7, 1) + timedelta(days=i), price, price + 0.5, price - 0.5, price, 20000)
        for i in range(n)
    ]


class TestDisabledByDefault(unittest.TestCase):
    def test_disabled_port_returns_weights_unchanged(self):
        port = DisabledAIAllocationPort()
        base = {"A": 0.6, "B": 0.4}
        result = port.recommend_adjustment(base, date(2026, 7, 1))
        self.assertEqual(result, base)

    def test_disabled_port_returns_a_copy_not_the_same_object(self):
        port = DisabledAIAllocationPort()
        base = {"A": 0.6, "B": 0.4}
        result = port.recommend_adjustment(base, date(2026, 7, 1))
        result["A"] = 0.99
        self.assertEqual(base["A"], 0.6, "Original dict must be unaffected by mutating the returned copy.")


class TestIdenticalResultsWhenDisabled(unittest.TestCase):
    def test_disabled_ai_produces_byte_identical_orders(self):
        price_history = {"A": bars(100, "A"), "B": bars(50, "B")}
        portfolio = PortfolioSnapshot(as_of_date=date(2026, 7, 1), cash=100000, positions={}, total_value=100000)
        base_weights = {"A": 0.6, "B": 0.4}

        strategy_no_ai = StrategyEngine(base_weights)
        orders_no_ai = strategy_no_ai.generate_orders(date(2026, 7, 1), price_history, portfolio)

        ai_port = DisabledAIAllocationPort()
        adjusted = ai_port.recommend_adjustment(base_weights, date(2026, 7, 1))
        strategy_with_ai = StrategyEngine(adjusted)
        orders_with_ai = strategy_with_ai.generate_orders(date(2026, 7, 1), price_history, portfolio)

        self.assertEqual(
            [(o.symbol, o.quantity, o.limit_price, o.side) for o in orders_no_ai],
            [(o.symbol, o.quantity, o.limit_price, o.side) for o in orders_with_ai],
        )


class TestNoExecutionCapability(unittest.TestCase):
    def test_interface_has_exactly_one_method(self):
        abstract_methods = AIAllocationPort.__abstractmethods__
        self.assertEqual(abstract_methods, frozenset({"recommend_adjustment"}))

    def test_method_signature_has_no_broker_or_order_parameters(self):
        sig = inspect.signature(AIAllocationPort.recommend_adjustment)
        param_names = {p.lower() for p in sig.parameters}
        for forbidden in ("broker", "order", "quantity", "submit", "execute"):
            self.assertFalse(
                any(forbidden in p for p in param_names),
                f"AIAllocationPort's signature must never reference '{forbidden}'.",
            )

    def test_no_broker_or_execution_manager_import_in_ai_allocation_package(self):
        import ast

        import etf_platform.ai_allocation.ports as ports_module

        tree = ast.parse(inspect.getsource(ports_module))
        imported_modules = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        ]
        for forbidden in ("execution_manager", "BrokerPort"):
            self.assertFalse(
                any(forbidden in m for m in imported_modules),
                f"ai_allocation.ports must not actually IMPORT anything containing '{forbidden}' "
                f"(mentioning it in a docstring, as this file does to explain the lack of coupling, is fine).",
            )


class TestModule28Independence(unittest.TestCase):
    def test_execution_manager_package_never_imports_ai_allocation(self):
        import etf_platform.execution_manager as em_pkg
        from pathlib import Path

        pkg_dir = Path(em_pkg.__file__).parent
        for py_file in pkg_dir.rglob("*.py"):
            source = py_file.read_text()
            self.assertNotIn("ai_allocation", source, f"{py_file} must never reference ai_allocation.")


class TestFrozenStrategyEngineUnmodified(unittest.TestCase):
    def test_strategy_engine_constructor_has_no_ai_parameter(self):
        sig = inspect.signature(StrategyEngine.__init__)
        for name in sig.parameters:
            self.assertNotIn("ai", name.lower())

    def test_strategy_engine_module_never_imports_ai_allocation(self):
        import etf_platform.strategy_engine.strategy as strategy_module

        source = inspect.getsource(strategy_module)
        self.assertNotIn("ai_allocation", source)


if __name__ == "__main__":
    unittest.main()
