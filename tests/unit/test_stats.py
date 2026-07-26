"""Unit tests for etf_optimizer.stats — the block bootstrap replacement
validation gate. This is the module the "no replacement without
statistically validated evidence" requirement most directly depends on, so
it gets the most rigorous test treatment in Phase 3."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import stats
from etf_platform.etf_optimizer.exceptions import InsufficientDataError


def bars_from_returns(returns: np.ndarray, start_price: float = 100.0, start: date = date(2025, 1, 1)) -> list[OHLCVBar]:
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [
        OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 10000)
        for i, p in enumerate(prices)
    ]


class TestBlockBootstrapMeanDiff(unittest.TestCase):
    def test_insufficient_length_raises(self) -> None:
        rng = np.random.default_rng(42)
        short = rng.normal(0, 0.01, 30)
        with self.assertRaises(InsufficientDataError):
            stats.block_bootstrap_mean_diff(short, short, rng=rng)

    def test_mismatched_length_raises(self) -> None:
        rng = np.random.default_rng(42)
        a = rng.normal(0, 0.01, 100)
        b = rng.normal(0, 0.01, 90)
        with self.assertRaises(ValueError):
            stats.block_bootstrap_mean_diff(a, b, rng=rng)

    def test_identical_series_gives_ci_around_zero(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0005, 0.01, 200)
        observed, ci_low, ci_high = stats.block_bootstrap_mean_diff(returns, returns, rng=rng, n_bootstrap=1000)
        self.assertAlmostEqual(observed, 0.0, places=6)
        self.assertLessEqual(ci_low, 0.0)
        self.assertGreaterEqual(ci_high, 0.0)

    def test_clearly_superior_candidate_gives_significant_positive_ci(self) -> None:
        rng = np.random.default_rng(42)
        n = 300
        incumbent_returns = rng.normal(0.0003, 0.01, n)
        # Candidate has a real, large, consistent daily edge — should be
        # unambiguously detected as significant.
        candidate_returns = incumbent_returns + 0.003
        observed, ci_low, ci_high = stats.block_bootstrap_mean_diff(
            candidate_returns, incumbent_returns, rng=rng, n_bootstrap=1000
        )
        self.assertGreater(observed, 0)
        self.assertGreater(ci_low, 0)  # CI entirely above zero -> significant

    def test_noise_only_difference_not_significant(self) -> None:
        rng = np.random.default_rng(7)
        n = 200
        base = rng.normal(0.0005, 0.01, n)
        # Two series with the same underlying process, independently noised
        # — any observed difference should be attributable to chance, and
        # the CI should not reliably exclude zero.
        candidate_returns = base + rng.normal(0, 0.0001, n)
        observed, ci_low, ci_high = stats.block_bootstrap_mean_diff(
            candidate_returns, base, rng=rng, n_bootstrap=1000
        )
        self.assertTrue(ci_low <= 0 <= ci_high)

    def test_confidence_interval_widens_with_lower_confidence_narrows_with_higher(self) -> None:
        rng = np.random.default_rng(1)
        n = 200
        a = rng.normal(0.001, 0.02, n)
        b = rng.normal(0.0, 0.02, n)
        _, low_90, high_90 = stats.block_bootstrap_mean_diff(a, b, rng=np.random.default_rng(1), confidence_level=0.90)
        _, low_99, high_99 = stats.block_bootstrap_mean_diff(a, b, rng=np.random.default_rng(1), confidence_level=0.99)
        self.assertLess(low_99, low_90)   # 99% CI is wider -> lower bound is lower
        self.assertGreater(high_99, high_90)  # and upper bound is higher


class TestValidateReplacement(unittest.TestCase):
    def test_insufficient_overlap_raises(self) -> None:
        rng = np.random.default_rng(42)
        short_returns = rng.normal(0, 0.01, 20)
        candidate_bars = bars_from_returns(short_returns, start=date(2025, 1, 1))
        incumbent_bars = bars_from_returns(short_returns, start=date(2025, 1, 1))
        with self.assertRaises(InsufficientDataError):
            stats.validate_replacement("CAND", "INCUMBENT", candidate_bars, incumbent_bars)

    def test_favors_candidate_when_clearly_better_and_no_worse_drawdown(self) -> None:
        rng = np.random.default_rng(42)
        n = 300
        incumbent_returns = rng.normal(0.0002, 0.008, n)
        candidate_returns = incumbent_returns + 0.002  # consistent edge, same vol profile
        candidate_bars = bars_from_returns(candidate_returns, start=date(2025, 1, 1))
        incumbent_bars = bars_from_returns(incumbent_returns, start=date(2025, 1, 1))

        result = stats.validate_replacement(
            "CAND", "INCUMBENT", candidate_bars, incumbent_bars, rng=np.random.default_rng(42)
        )
        self.assertTrue(result.is_significant)
        self.assertTrue(result.favors_candidate)

    def test_does_not_favor_candidate_when_no_real_edge(self) -> None:
        rng = np.random.default_rng(7)
        n = 200
        base_returns = rng.normal(0.0003, 0.01, n)
        candidate_bars = bars_from_returns(base_returns + rng.normal(0, 0.0001, n), start=date(2025, 1, 1))
        incumbent_bars = bars_from_returns(base_returns, start=date(2025, 1, 1))

        result = stats.validate_replacement(
            "CAND", "INCUMBENT", candidate_bars, incumbent_bars, rng=np.random.default_rng(7)
        )
        self.assertFalse(result.favors_candidate)

    def test_drawdown_worse_flag_set_correctly(self) -> None:
        rng = np.random.default_rng(3)
        n = 300
        incumbent_returns = rng.normal(0.0002, 0.005, n)
        # Candidate: better average return, but with an engineered severe
        # drawdown partway through (a crash-then-recover pattern) so its
        # max drawdown is clearly worse despite the better mean return.
        candidate_returns = incumbent_returns.copy() + 0.0015
        candidate_returns[100] = -0.30  # single severe crash day

        candidate_bars = bars_from_returns(candidate_returns, start=date(2025, 1, 1))
        incumbent_bars = bars_from_returns(incumbent_returns, start=date(2025, 1, 1))

        result = stats.validate_replacement(
            "CAND", "INCUMBENT", candidate_bars, incumbent_bars, rng=np.random.default_rng(3)
        )
        self.assertTrue(result.drawdown_worse)
        self.assertGreater(result.candidate_max_drawdown, result.incumbent_max_drawdown)

    def test_n_observations_matches_overlap(self) -> None:
        rng = np.random.default_rng(1)
        n = 150
        returns = rng.normal(0, 0.01, n)
        candidate_bars = bars_from_returns(returns, start=date(2025, 1, 1))
        incumbent_bars = bars_from_returns(returns, start=date(2025, 1, 1))
        result = stats.validate_replacement(
            "CAND", "INCUMBENT", candidate_bars, incumbent_bars, rng=np.random.default_rng(1)
        )
        self.assertEqual(result.n_observations, n)  # n price points -> n returns from bars_from_returns' construction


if __name__ == "__main__":
    unittest.main()
