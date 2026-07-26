"""Unit tests for MonteCarloSimulator."""

from __future__ import annotations

import unittest

import numpy as np

from etf_platform.validation.monte_carlo import MonteCarloSimulator


class TestMonteCarloSimulatorConstruction(unittest.TestCase):
    def test_too_few_simulations_raises(self) -> None:
        with self.assertRaises(ValueError):
            MonteCarloSimulator(n_simulations=10)

    def test_invalid_block_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            MonteCarloSimulator(n_simulations=500, block_size=0)


class TestMonteCarloSimulate(unittest.TestCase):
    def test_insufficient_data_raises(self) -> None:
        sim = MonteCarloSimulator(n_simulations=500, block_size=20, rng=np.random.default_rng(1))
        with self.assertRaises(ValueError):
            sim.simulate(np.array([0.01] * 5), starting_value=100000)

    def test_percentiles_ordered_correctly(self) -> None:
        rng = np.random.default_rng(1)
        returns = rng.normal(0.0005, 0.01, 500)
        sim = MonteCarloSimulator(n_simulations=500, block_size=20, rng=np.random.default_rng(2))
        result = sim.simulate(returns, starting_value=100000)

        p = result.final_value_percentiles
        self.assertLessEqual(p[5], p[25])
        self.assertLessEqual(p[25], p[50])
        self.assertLessEqual(p[50], p[75])
        self.assertLessEqual(p[75], p[95])

    def test_zero_return_series_gives_flat_outcome(self) -> None:
        returns = np.zeros(200)
        sim = MonteCarloSimulator(n_simulations=200, block_size=10, rng=np.random.default_rng(3))
        result = sim.simulate(returns, starting_value=100000)
        self.assertAlmostEqual(result.final_value_percentiles[50], 100000, places=2)
        self.assertAlmostEqual(result.probability_of_loss, 0.0, places=2)

    def test_positive_drift_gives_low_probability_of_loss(self) -> None:
        rng = np.random.default_rng(4)
        # Strong positive drift, modest volatility -> most simulated paths should end up.
        returns = rng.normal(0.002, 0.005, 300)
        sim = MonteCarloSimulator(n_simulations=1000, block_size=20, rng=np.random.default_rng(5))
        result = sim.simulate(returns, starting_value=100000)
        self.assertLess(result.probability_of_loss, 0.3)

    def test_negative_drift_gives_high_probability_of_loss(self) -> None:
        rng = np.random.default_rng(6)
        returns = rng.normal(-0.002, 0.005, 300)
        sim = MonteCarloSimulator(n_simulations=1000, block_size=20, rng=np.random.default_rng(7))
        result = sim.simulate(returns, starting_value=100000)
        self.assertGreater(result.probability_of_loss, 0.7)

    def test_max_drawdown_percentiles_are_nonnegative(self) -> None:
        rng = np.random.default_rng(8)
        returns = rng.normal(0.0003, 0.02, 300)
        sim = MonteCarloSimulator(n_simulations=500, block_size=15, rng=np.random.default_rng(9))
        result = sim.simulate(returns, starting_value=100000)
        for pct_value in result.max_drawdown_percentiles.values():
            self.assertGreaterEqual(pct_value, 0.0)

    def test_deterministic_with_seeded_rng(self) -> None:
        returns = np.random.default_rng(1).normal(0.0005, 0.01, 300)
        result1 = MonteCarloSimulator(500, 20, rng=np.random.default_rng(42)).simulate(returns, 100000)
        result2 = MonteCarloSimulator(500, 20, rng=np.random.default_rng(42)).simulate(returns, 100000)
        self.assertEqual(result1.final_value_percentiles, result2.final_value_percentiles)


if __name__ == "__main__":
    unittest.main()
