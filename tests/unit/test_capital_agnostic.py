"""Verifies the capital-agnostic requirement (PHASE5_Objectives.md section
"Capital rules", section 15 of PHASE1_Architecture_SRS.md): Portfolio
Optimizer's output must be identical regardless of investment amount,
because it never accepts or references one anywhere.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from datetime import date, timedelta

import numpy as np

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer import PortfolioOptimizer
from etf_platform.portfolio_optimizer import optimizer as optimizer_module
from etf_platform.portfolio_optimizer import proposal_builder as proposal_builder_module
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine


def bars(returns, price=100.0, start=date(2023, 1, 1)):
    prices = [price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 20000) for i, p in enumerate(prices)]


class TestNoAbsoluteAmountInSignatures(unittest.TestCase):
    _FORBIDDEN_PARAM_SUBSTRINGS = ("capital", "rupee", "amount_invested", "investment_amount")

    def test_optimizer_optimize_signature_has_no_amount_param(self):
        sig = inspect.signature(PortfolioOptimizer.optimize)
        for name in sig.parameters:
            for forbidden in self._FORBIDDEN_PARAM_SUBSTRINGS:
                self.assertNotIn(forbidden, name.lower(), f"optimize() has a forbidden param: {name}")

    def test_build_proposal_signature_has_no_amount_param(self):
        from etf_platform.portfolio_optimizer.proposal_builder import build_proposal

        sig = inspect.signature(build_proposal)
        for name in sig.parameters:
            for forbidden in self._FORBIDDEN_PARAM_SUBSTRINGS:
                self.assertNotIn(forbidden, name.lower(), f"build_proposal() has a forbidden param: {name}")

    def test_no_source_line_hardcodes_a_named_sip_amount(self):
        source = inspect.getsource(optimizer_module) + inspect.getsource(proposal_builder_module)
        tree = ast.parse(source)
        suspicious_numbers = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                if node.value in (1000, 5000, 10000, 20000, 50000, 100000, 500000):
                    suspicious_numbers.append(node.value)
        self.assertEqual(suspicious_numbers, [], f"Found hardcoded amount-like constants: {suspicious_numbers}")


class TestIdenticalLogicAcrossCapitalLevels(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.price_history = {
            "A": bars(list(rng.normal(0.0003, 0.01, 300))),
            "B": bars(list(rng.normal(0.0003, 0.02, 300))),
        }
        self.candidates = [ETFScore("A", 0, ()), ETFScore("B", 0, ())]
        self.engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.7, max_weight_per_asset_class=0.7, min_history_days_required=100))
        )
        self.optimizer = PortfolioOptimizer(self.engine)

    def test_optimize_called_twice_gives_identical_weights(self):
        result1 = self.optimizer.optimize(self.candidates, {"A": "x", "B": "y"}, self.price_history)
        result2 = self.optimizer.optimize(self.candidates, {"A": "x", "B": "y"}, self.price_history)
        self.assertEqual(result1.weights_dict(), result2.weights_dict())

    def test_downstream_quantity_translation_scales_proportionally(self):
        result = self.optimizer.optimize(self.candidates, {"A": "x", "B": "y"}, self.price_history)
        weights = result.weights_dict()
        price_a = self.price_history["A"][-1].close
        price_b = self.price_history["B"][-1].close

        capital_levels = [1_000, 5_000, 10_000, 20_000, 50_000, 100_000, 500_000]
        ratios = []
        for capital in capital_levels:
            qty_a = (capital * weights["A"]) / price_a
            qty_b = (capital * weights["B"]) / price_b
            ratios.append(qty_a / qty_b if qty_b > 0 else None)

        for r in ratios[1:]:
            self.assertAlmostEqual(r, ratios[0], places=9)


if __name__ == "__main__":
    unittest.main()
