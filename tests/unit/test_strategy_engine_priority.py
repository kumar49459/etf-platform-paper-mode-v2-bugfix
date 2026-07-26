"""Tests for priority.py - buy-only diff and largest-gap-first ordering."""

from __future__ import annotations

import unittest

from etf_platform.strategy_engine.priority import compute_buy_only_diff, prioritize_by_gap


class TestBuyOnlyDiff(unittest.TestCase):
    def test_weight_increase_becomes_gap(self):
        gaps = compute_buy_only_diff({"A": 0.2}, {"A": 0.5})
        self.assertAlmostEqual(gaps["A"], 0.3)

    def test_weight_decrease_never_becomes_negative_gap(self):
        gaps = compute_buy_only_diff({"A": 0.5}, {"A": 0.2})
        self.assertNotIn("A", gaps)

    def test_new_symbol_not_held_is_a_gap(self):
        gaps = compute_buy_only_diff({}, {"NEW": 0.3})
        self.assertAlmostEqual(gaps["NEW"], 0.3)

    def test_held_symbol_dropped_from_target_is_never_a_gap(self):
        gaps = compute_buy_only_diff({"OLD": 0.4}, {})
        self.assertEqual(gaps, {})

    def test_unchanged_weight_produces_no_gap(self):
        gaps = compute_buy_only_diff({"A": 0.3}, {"A": 0.3})
        self.assertEqual(gaps, {})

    def test_no_gap_value_is_ever_negative_or_zero(self):
        import random

        rng = random.Random(1)
        for _ in range(50):
            current = {s: rng.uniform(0, 0.6) for s in "ABCDE"}
            target = {s: rng.uniform(0, 0.6) for s in "ABCDE"}
            gaps = compute_buy_only_diff(current, target)
            for gap in gaps.values():
                self.assertGreater(gap, 0)


class TestPrioritizeByGap(unittest.TestCase):
    def test_largest_gap_ranked_first(self):
        opportunities = prioritize_by_gap({"A": 0.1, "B": 0.1}, {"A": 0.5, "B": 0.2})
        self.assertEqual(opportunities[0].symbol, "A")
        self.assertEqual(opportunities[1].symbol, "B")

    def test_equal_gaps_broken_alphabetically_for_determinism(self):
        opportunities = prioritize_by_gap({"Z": 0.0, "A": 0.0}, {"Z": 0.3, "A": 0.3})
        self.assertEqual(opportunities[0].symbol, "A")
        self.assertEqual(opportunities[1].symbol, "Z")

    def test_repeated_calls_produce_identical_order(self):
        current, target = {"A": 0.1, "B": 0.2, "C": 0.05}, {"A": 0.3, "B": 0.3, "C": 0.3}
        first = [o.symbol for o in prioritize_by_gap(current, target)]
        second = [o.symbol for o in prioritize_by_gap(current, target)]
        self.assertEqual(first, second)

    def test_no_opportunities_when_at_or_above_target(self):
        opportunities = prioritize_by_gap({"A": 0.5}, {"A": 0.3})
        self.assertEqual(opportunities, [])


if __name__ == "__main__":
    unittest.main()
