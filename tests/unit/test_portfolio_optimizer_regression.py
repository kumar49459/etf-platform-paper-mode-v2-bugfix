"""Regression test locking in exact Portfolio Optimizer output for a fixed,
deterministic scenario (same discipline as Phase 4's
test_backtest_regression.py). If this test ever fails, understand why
before updating the expected values - don't just make it pass.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer import PortfolioOptimizer
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine


def _make_bars(closes, start=date(2024, 1, 1)):
    return [
        OHLCVBar("X", start + timedelta(days=i), c - 0.2, c + 0.2, c - 0.4, c, 20000)
        for i, c in enumerate(closes)
    ]


class TestPortfolioOptimizerRegressionBaseline(unittest.TestCase):
    def setUp(self):
        closes_a = [100 + (i % 10) * 0.5 - 2.5 for i in range(300)]
        closes_b = [100 + (i % 4) * 2 - 3 for i in range(300)]
        closes_c = [100 + (i % 20) * 0.2 - 2 for i in range(300)]

        self.price_history = {"A": _make_bars(closes_a), "B": _make_bars(closes_b), "C": _make_bars(closes_c)}
        self.candidates = [ETFScore("A", 0, ()), ETFScore("B", 0, ()), ETFScore("C", 0, ())]
        self.asset_classes = {"A": "equity", "B": "equity", "C": "gold"}
        self.engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(
                max_weight_per_etf=0.5, max_weight_per_asset_class=0.7, min_history_days_required=100
            ))
        )
        self.optimizer = PortfolioOptimizer(self.engine)

    def test_locked_target_weights(self):
        result = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        self.assertTrue(result.feasible)

        weights = result.weights_dict()
        self.assertAlmostEqual(weights["A"], 0.34976833445962446, places=8)
        self.assertAlmostEqual(weights["B"], 0.1502316655403755, places=8)
        self.assertAlmostEqual(weights["C"], 0.5, places=8)

        self.assertAlmostEqual(result.cash_reserve_pct, 0.0, places=8)
        self.assertAlmostEqual(result.total_invested_pct(), 1.0, places=8)

    def test_deterministic_across_repeated_runs(self):
        result1 = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        engine2 = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(
                max_weight_per_etf=0.5, max_weight_per_asset_class=0.7, min_history_days_required=100
            ))
        )
        result2 = PortfolioOptimizer(engine2).optimize(self.candidates, self.asset_classes, self.price_history)
        self.assertEqual(result1.weights_dict(), result2.weights_dict())


if __name__ == "__main__":
    unittest.main()
