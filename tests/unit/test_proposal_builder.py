"""Tests for proposal_builder.py - heaviest focus on the buy-only diff,
since that's the literal enforcement point of the manual-selling rule."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer import PortfolioOptimizer, build_proposal
from etf_platform.portfolio_optimizer.models import OptimizationMethod, OptimizationResult, TargetWeight
from etf_platform.portfolio_optimizer.proposal_builder import _buy_only_diff
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine


def bars(returns, price=100.0, start=date(2023, 1, 1)):
    prices = [price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 20000) for i, p in enumerate(prices)]


class TestBuyOnlyDiff(unittest.TestCase):
    def test_weight_increase_becomes_buy_change(self):
        buy_changes, notes = _buy_only_diff({"A": 0.2}, {"A": 0.5})
        self.assertEqual(buy_changes, {"A": 0.3})
        self.assertEqual(notes, ())

    def test_weight_decrease_never_becomes_a_sell_instruction(self):
        buy_changes, notes = _buy_only_diff({"A": 0.5}, {"A": 0.2})
        self.assertEqual(buy_changes, {})
        self.assertEqual(len(notes), 1)
        self.assertIn("No sell will be proposed", notes[0])

    def test_new_target_symbol_not_currently_held_is_a_buy(self):
        buy_changes, notes = _buy_only_diff({}, {"NEW": 0.3})
        self.assertEqual(buy_changes, {"NEW": 0.3})

    def test_held_symbol_absent_from_target_is_informational_only(self):
        buy_changes, notes = _buy_only_diff({"OLD": 0.4}, {})
        self.assertEqual(buy_changes, {})
        self.assertEqual(len(notes), 1)
        self.assertIn("No sell will be proposed", notes[0])

    def test_unchanged_weight_produces_no_change_and_no_note(self):
        buy_changes, notes = _buy_only_diff({"A": 0.3}, {"A": 0.3})
        self.assertEqual(buy_changes, {})
        self.assertEqual(notes, ())

    def test_mixed_scenario_only_increases_are_actionable(self):
        current = {"A": 0.5, "B": 0.1, "C": 0.2}
        target = {"A": 0.2, "B": 0.4, "C": 0.2, "D": 0.1}
        buy_changes, notes = _buy_only_diff(current, target)
        self.assertEqual(set(buy_changes), {"B", "D"})
        self.assertNotIn("A", buy_changes)
        self.assertEqual(len(notes), 1)
        self.assertIn("A", notes[0])

    def test_no_buy_change_value_is_ever_negative(self):
        rng = np.random.default_rng(1)
        for _ in range(50):
            current = {s: max(0, v) for s, v in zip("ABCDE", rng.uniform(-0.1, 0.6, 5))}
            target = {s: max(0, v) for s, v in zip("ABCDE", rng.uniform(-0.1, 0.6, 5))}
            buy_changes, _ = _buy_only_diff(current, target)
            for delta in buy_changes.values():
                self.assertGreater(delta, 0)


class TestProposalBuilderIntegration(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.price_history = {
            "NIFTYBEES": bars(list(rng.normal(0.0004, 0.011, 500)), 250),
            "GOLDBEES": bars(list(rng.normal(0.0003, 0.007, 500)), 68),
        }
        self.candidates = [ETFScore("NIFTYBEES", 0, ()), ETFScore("GOLDBEES", 0, ())]
        self.asset_classes = {"NIFTYBEES": "equity", "GOLDBEES": "gold"}
        self.risk_engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.7, max_weight_per_asset_class=0.7, min_history_days_required=100))
        )
        self.optimizer = PortfolioOptimizer(self.risk_engine)
        self.as_of = date(2023, 1, 1) + timedelta(days=500)

    def test_infeasible_result_raises(self):
        infeasible = OptimizationResult(feasible=False, infeasibility_reason="test")
        with self.assertRaises(ValueError):
            build_proposal(infeasible, {}, {}, self.price_history, self.risk_engine)

    def test_proposal_contains_all_approval_console_required_fields(self):
        result = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        proposal = build_proposal(
            result, {"NIFTYBEES": 0.9, "GOLDBEES": 0.0}, self.asset_classes, self.price_history,
            self.risk_engine, as_of=self.as_of,
        )
        self.assertTrue(proposal.current_weights)
        self.assertTrue(proposal.recommended_weights)
        self.assertTrue(proposal.reason)
        self.assertIsNotNone(proposal.confidence_score)
        self.assertIsInstance(proposal.cost_impact_pct, float)
        self.assertIn("current_allocation", proposal.supporting_backtest_summary)
        self.assertIn("candidate_allocation", proposal.supporting_backtest_summary)

    def test_no_current_holdings_treats_all_as_new_buys(self):
        result = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        proposal = build_proposal(result, {}, self.asset_classes, self.price_history, self.risk_engine, as_of=self.as_of)
        self.assertEqual(set(proposal.buy_only_changes), set(result.weights_dict()))
        self.assertEqual(proposal.overweight_notes, ())

    def test_cost_impact_is_percentage_not_absolute(self):
        result = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        proposal = build_proposal(result, {}, self.asset_classes, self.price_history, self.risk_engine, as_of=self.as_of)
        self.assertLess(proposal.cost_impact_pct, 0.05)
        self.assertGreater(proposal.cost_impact_pct, 0.0)
        self.assertTrue(proposal.cost_impact_caveat)

    def test_risk_analysis_events_carry_no_sell_instruction(self):
        """Every risk event surfaced in a real proposal must have survived
        RiskEvent's own construction-time manual-selling guard -- if any
        event here contained a real sell instruction, RiskEvent's
        __post_init__ would have already raised during evaluate()."""
        result = self.optimizer.optimize(self.candidates, self.asset_classes, self.price_history)
        proposal = build_proposal(
            result, {"NIFTYBEES": 0.9, "GOLDBEES": 0.0}, self.asset_classes, self.price_history,
            self.risk_engine, as_of=self.as_of,
        )
        self.assertGreater(len(proposal.risk_analysis), 0)  # sanity: this scenario does trigger events

    def test_insufficient_history_gives_none_confidence_not_crash(self):
        short_history = {"NIFTYBEES": bars([0.001] * 10), "GOLDBEES": bars([0.001] * 10)}
        fake_result = OptimizationResult(
            feasible=True, method_used=OptimizationMethod.INVERSE_VOLATILITY,
            target_weights=(
                TargetWeight(symbol="NIFTYBEES", weight=0.5, method_used=OptimizationMethod.INVERSE_VOLATILITY, components=()),
                TargetWeight(symbol="GOLDBEES", weight=0.5, method_used=OptimizationMethod.INVERSE_VOLATILITY, components=()),
            ),
        )
        proposal = build_proposal(
            fake_result, {}, self.asset_classes, short_history, self.risk_engine, as_of=date(2023, 1, 11),
        )
        self.assertIsNone(proposal.confidence_score)
        self.assertTrue(proposal.confidence_note)


if __name__ == "__main__":
    unittest.main()
