"""Unit/integration tests for PortfolioOptimizer, focused heavily on the
hard-constraint capping ("water-filling") algorithm since it's the most
mathematically complex piece of Phase 5."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer.exceptions import EmptyCandidateUniverseError, MethodNotRegisteredError
from etf_platform.portfolio_optimizer.methods.base import get_method
from etf_platform.portfolio_optimizer.models import OptimizationMethod
from etf_platform.portfolio_optimizer.optimizer import PortfolioOptimizer
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine


def bars_from_returns(returns, start_price=100.0, start=date(2024, 1, 1)):
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 10000) for i, p in enumerate(prices)]


def make_score(symbol):
    return ETFScore(symbol=symbol, composite_score=0.0, metric_scores=())


class TestMethodRegistryIntegration(unittest.TestCase):
    def test_inverse_volatility_is_registered_on_import(self):
        method = get_method(OptimizationMethod.INVERSE_VOLATILITY)
        self.assertIsNotNone(method)

    def test_unregistered_method_raises_not_silently_falls_back(self):
        with self.assertRaises(MethodNotRegisteredError):
            get_method(OptimizationMethod.RISK_PARITY)


class TestPortfolioOptimizerBasics(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(10)
        self.engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=1.0, max_weight_per_asset_class=1.0, min_history_days_required=100))
        )
        self.optimizer = PortfolioOptimizer(self.engine)

    def test_empty_universe_raises(self):
        with self.assertRaises(EmptyCandidateUniverseError):
            self.optimizer.optimize([], {}, {})

    def test_uncapped_weights_sum_to_approximately_one(self):
        candidates = [make_score(s) for s in ["A", "B", "C"]]
        price_history = {
            s: bars_from_returns(list(self.rng.normal(0.0003, 0.01 * (i + 1), 300)))
            for i, s in enumerate(["A", "B", "C"])
        }
        result = self.optimizer.optimize(candidates, {"A": "eq", "B": "eq", "C": "gold"}, price_history)
        self.assertTrue(result.feasible)
        self.assertAlmostEqual(result.total_invested_pct(), 1.0, places=4)
        self.assertAlmostEqual(result.cash_reserve_pct, 0.0, places=4)

    def test_insufficient_history_symbol_excluded_with_reason(self):
        candidates = [make_score("SHORT"), make_score("LONG")]
        price_history = {
            "SHORT": bars_from_returns(list(self.rng.normal(0.0003, 0.01, 10))),
            "LONG": bars_from_returns(list(self.rng.normal(0.0003, 0.01, 300))),
        }
        result = self.optimizer.optimize(candidates, {"SHORT": "eq", "LONG": "eq"}, price_history)
        excluded_symbols = {s for s, _ in result.excluded_symbols}
        self.assertIn("SHORT", excluded_symbols)
        self.assertNotIn("LONG", excluded_symbols)

    def test_all_excluded_gives_infeasible_result(self):
        candidates = [make_score("A")]
        price_history = {"A": bars_from_returns([0.001] * 5)}
        result = self.optimizer.optimize(candidates, {"A": "eq"}, price_history)
        self.assertFalse(result.feasible)
        self.assertTrue(result.infeasibility_reason)


class TestHardCapEnforcement(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(20)

    def test_per_etf_cap_never_exceeded(self):
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.30, max_weight_per_asset_class=1.0, min_history_days_required=100))
        )
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["A", "B", "C"]]
        price_history = {
            "A": bars_from_returns(list(self.rng.normal(0.0003, 0.002, 300))),
            "B": bars_from_returns(list(self.rng.normal(0.0003, 0.02, 300))),
            "C": bars_from_returns(list(self.rng.normal(0.0003, 0.02, 300))),
        }
        result = optimizer.optimize(candidates, {"A": "eq", "B": "eq", "C": "eq"}, price_history)
        for tw in result.target_weights:
            self.assertLessEqual(tw.weight, 0.30 + 1e-6, f"{tw.symbol} exceeded the 30% cap: {tw.weight}")

    def test_capped_excess_redistributed_to_others(self):
        # NOTE: with a *very* tight cap (e.g. 30% across only 3 symbols),
        # ALL three can legitimately end up saturated at the cap after
        # redistribution, correctly leaving cash reserve rather than
        # reaching 100% — that's not a bug, see
        # test_cash_reserve_when_caps_cannot_reach_full_investment. This
        # test uses a looser cap specifically so redistribution can be
        # checked without every symbol saturating.
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.50, max_weight_per_asset_class=1.0, min_history_days_required=100))
        )
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["A", "B", "C"]]
        price_history = {
            "A": bars_from_returns(list(self.rng.normal(0.0003, 0.002, 300))),
            "B": bars_from_returns(list(self.rng.normal(0.0003, 0.02, 300))),
            "C": bars_from_returns(list(self.rng.normal(0.0003, 0.02, 300))),
        }
        result = optimizer.optimize(candidates, {"A": "eq", "B": "eq", "C": "eq"}, price_history)
        self.assertAlmostEqual(result.total_invested_pct(), 1.0, places=3)
        weights_by_symbol = result.weights_dict()
        self.assertAlmostEqual(weights_by_symbol["A"], 0.50, places=3)  # capped
        # B and C should have absorbed the redistributed excess roughly
        # equally (similar, not identical, volatility profiles from
        # independent random draws in this fixture).
        self.assertAlmostEqual(weights_by_symbol["B"], weights_by_symbol["C"], delta=0.02)

    def test_asset_class_cap_never_exceeded(self):
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.40, max_weight_per_asset_class=0.40, min_history_days_required=100))
        )
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["EQ1", "EQ2", "GOLD"]]
        price_history = {
            "EQ1": bars_from_returns(list(self.rng.normal(0.0003, 0.01, 300))),
            "EQ2": bars_from_returns(list(self.rng.normal(0.0003, 0.01, 300))),
            "GOLD": bars_from_returns(list(self.rng.normal(0.0003, 0.05, 300))),
        }
        result = optimizer.optimize(
            candidates, {"EQ1": "equity", "EQ2": "equity", "GOLD": "gold"}, price_history
        )
        equity_total = sum(tw.weight for tw in result.target_weights if tw.symbol in ("EQ1", "EQ2"))
        self.assertLessEqual(equity_total, 0.40 + 1e-6)

    def test_both_caps_together_still_never_violated(self):
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.15, max_weight_per_asset_class=0.35, min_history_days_required=100))
        )
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["A", "B", "C", "D", "E"]]
        price_history = {
            s: bars_from_returns(list(self.rng.normal(0.0003, 0.005 * (i + 1), 300)))
            for i, s in enumerate(["A", "B", "C", "D", "E"])
        }
        asset_classes = {"A": "eq", "B": "eq", "C": "eq", "D": "gold", "E": "gold"}
        result = optimizer.optimize(candidates, asset_classes, price_history)

        for tw in result.target_weights:
            self.assertLessEqual(tw.weight, 0.15 + 1e-6)
        class_totals = {}
        for tw in result.target_weights:
            ac = asset_classes[tw.symbol]
            class_totals[ac] = class_totals.get(ac, 0.0) + tw.weight
        for ac, total in class_totals.items():
            self.assertLessEqual(total, 0.35 + 1e-6, f"asset class {ac} exceeded cap: {total}")

    def test_cash_reserve_when_caps_cannot_reach_full_investment(self):
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.20, max_weight_per_asset_class=1.0, min_history_days_required=100))
        )
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["A", "B", "C"]]
        price_history = {
            s: bars_from_returns(list(self.rng.normal(0.0003, 0.01, 300))) for s in ["A", "B", "C"]
        }
        result = optimizer.optimize(candidates, {"A": "eq", "B": "eq", "C": "eq"}, price_history)
        self.assertTrue(result.feasible)
        self.assertAlmostEqual(result.total_invested_pct(), 0.60, places=2)
        self.assertAlmostEqual(result.cash_reserve_pct, 0.40, places=2)
        for tw in result.target_weights:
            self.assertLessEqual(tw.weight, 0.20 + 1e-6)


class TestExplainability(unittest.TestCase):
    def test_every_target_weight_has_explanation(self):
        rng = np.random.default_rng(30)
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(min_history_days_required=100)))
        optimizer = PortfolioOptimizer(engine)
        candidates = [make_score(s) for s in ["A", "B"]]
        price_history = {s: bars_from_returns(list(rng.normal(0.0003, 0.01, 300))) for s in ["A", "B"]}
        result = optimizer.optimize(candidates, {"A": "eq", "B": "eq"}, price_history)
        for tw in result.target_weights:
            self.assertTrue(tw.explanation)
            self.assertIn(tw.symbol, tw.explanation)


if __name__ == "__main__":
    unittest.main()
