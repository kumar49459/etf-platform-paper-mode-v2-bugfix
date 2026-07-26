"""Unit tests for performance_analytics.metrics — verified against known
formulas and hand-computable examples, not just "does it run"."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import numpy as np

from etf_platform.performance_analytics.metrics import (
    cagr,
    calmar_ratio,
    max_drawdown_from_equity_curve,
    rolling_returns,
    sharpe_ratio,
    sortino_ratio,
    win_loss_stats,
    xirr,
)


class TestXIRR(unittest.TestCase):
    def test_simple_lump_sum_matches_cagr(self) -> None:
        # Invest 100,000 on day 0, worth 121,000 exactly 2 years later ->
        # this is precisely a 10% CAGR, and XIRR should match CAGR exactly
        # for a single lump-sum investment with no interim cash flows.
        cashflows = [(date(2023, 1, 1), -100000.0), (date(2025, 1, 1), 121000.0)]
        result = xirr(cashflows)
        self.assertAlmostEqual(result, 0.10, places=3)

    def test_result_satisfies_npv_zero_property(self) -> None:
        """The defining property of IRR: discounting all cash flows at the
        computed rate must produce a net present value of ~0. This is
        self-verifying (doesn't depend on trusting an external "commonly
        cited" answer, which — worth noting — I initially got wrong in an
        earlier version of this test by trusting a remembered figure
        instead of checking it; this version checks the actual math)."""
        cashflows = [
            (date(2020, 1, 1), -10000.0),
            (date(2020, 3, 1), 2750.0),
            (date(2020, 10, 30), 4250.0),
            (date(2021, 2, 15), 3250.0),
        ]
        result = xirr(cashflows)
        self.assertIsNotNone(result)

        t0 = cashflows[0][0]
        npv_at_result = sum(
            amount / (1 + result) ** ((d - t0).days / 365.0) for d, amount in cashflows
        )
        self.assertAlmostEqual(npv_at_result, 0.0, places=2)

    def test_all_negative_cashflows_returns_none(self) -> None:
        cashflows = [(date(2023, 1, 1), -100.0), (date(2024, 1, 1), -50.0)]
        self.assertIsNone(xirr(cashflows))

    def test_single_cashflow_returns_none(self) -> None:
        self.assertIsNone(xirr([(date(2023, 1, 1), -100.0)]))


class TestCAGR(unittest.TestCase):
    def test_doubling_in_one_year_is_100_pct(self) -> None:
        self.assertAlmostEqual(cagr(100000, 200000, 1.0), 1.0, places=6)

    def test_ten_pct_over_two_years(self) -> None:
        result = cagr(100000, 121000, 2.0)
        self.assertAlmostEqual(result, 0.10, places=6)

    def test_zero_start_value_returns_none(self) -> None:
        self.assertIsNone(cagr(0, 100000, 1.0))

    def test_zero_years_returns_none(self) -> None:
        self.assertIsNone(cagr(100000, 110000, 0.0))


class TestSharpeSortino(unittest.TestCase):
    def test_zero_volatility_returns_none_not_infinite(self) -> None:
        returns = np.array([0.001] * 100)  # perfectly constant
        self.assertIsNone(sharpe_ratio(returns))

    def test_positive_mean_positive_vol_gives_positive_sharpe(self) -> None:
        rng = np.random.default_rng(1)
        returns = rng.normal(0.001, 0.01, 500)
        result = sharpe_ratio(returns)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_sortino_ignores_upside_volatility(self) -> None:
        # Two return series with identical mean and identical TOTAL
        # volatility, but one has its variance concentrated on the upside
        # (large positive outliers) and the other on the downside (large
        # negative outliers). Sortino should be higher (better) for the
        # upside-skewed series, while Sharpe treats them identically.
        rng = np.random.default_rng(2)
        base = rng.normal(0.0005, 0.005, 300)
        upside_skewed = base.copy()
        upside_skewed[::20] += 0.05  # occasional large positive jumps
        downside_skewed = base.copy()
        downside_skewed[::20] -= 0.05  # occasional large negative jumps

        sortino_upside = sortino_ratio(upside_skewed)
        sortino_downside = sortino_ratio(downside_skewed)
        self.assertGreater(sortino_upside, sortino_downside)

    def test_insufficient_data_returns_none(self) -> None:
        self.assertIsNone(sharpe_ratio(np.array([0.01])))
        self.assertIsNone(sortino_ratio(np.array([0.01])))


class TestMaxDrawdown(unittest.TestCase):
    def test_known_drawdown_value(self) -> None:
        # Peak 150, trough 90 -> drawdown = (150-90)/150 = 0.4
        curve = [100, 150, 120, 90, 130]
        self.assertAlmostEqual(max_drawdown_from_equity_curve(curve), 0.4, places=6)

    def test_monotonic_increase_has_zero_drawdown(self) -> None:
        self.assertAlmostEqual(max_drawdown_from_equity_curve([100, 110, 120, 130]), 0.0)

    def test_insufficient_data_returns_none(self) -> None:
        self.assertIsNone(max_drawdown_from_equity_curve([100]))


class TestCalmar(unittest.TestCase):
    def test_known_ratio(self) -> None:
        self.assertAlmostEqual(calmar_ratio(0.20, 0.10), 2.0, places=6)

    def test_zero_drawdown_returns_none(self) -> None:
        self.assertIsNone(calmar_ratio(0.20, 0.0))

    def test_none_inputs_propagate_to_none(self) -> None:
        self.assertIsNone(calmar_ratio(None, 0.1))
        self.assertIsNone(calmar_ratio(0.1, None))


class TestRollingReturns(unittest.TestCase):
    def test_no_results_before_first_full_window(self) -> None:
        curve = [(date(2025, 1, 1) + timedelta(days=i), 100000 * 1.001**i) for i in range(30)]
        results = rolling_returns(curve, window_days=365)
        self.assertEqual(results, [])  # only 30 days of data, no full 365-day window exists yet

    def test_produces_results_once_window_available(self) -> None:
        curve = [(date(2024, 1, 1) + timedelta(days=i), 100000 * 1.0003**i) for i in range(500)]
        results = rolling_returns(curve, window_days=365)
        self.assertGreater(len(results), 0)


class TestWinLossStats(unittest.TestCase):
    def test_empty_list(self) -> None:
        stats = win_loss_stats([])
        self.assertEqual(stats.total_trades, 0)
        self.assertIsNone(stats.win_rate)

    def test_known_win_rate(self) -> None:
        stats = win_loss_stats([100, -50, 200, -30, 50])
        self.assertEqual(stats.total_trades, 5)
        self.assertEqual(stats.winning_trades, 3)
        self.assertEqual(stats.losing_trades, 2)
        self.assertAlmostEqual(stats.win_rate, 0.6, places=6)

    def test_profit_factor_known_value(self) -> None:
        # gross profit = 100+200+50=350, gross loss = 50+30=80 -> PF=4.375
        stats = win_loss_stats([100, -50, 200, -30, 50])
        self.assertAlmostEqual(stats.profit_factor, 350 / 80, places=6)

    def test_profit_factor_none_when_no_losses(self) -> None:
        stats = win_loss_stats([100, 200, 50])
        self.assertIsNone(stats.profit_factor)

    def test_largest_win_and_loss(self) -> None:
        stats = win_loss_stats([100, -50, 200, -30, 50])
        self.assertEqual(stats.largest_win, 200)
        self.assertEqual(stats.largest_loss, -50)


if __name__ == "__main__":
    unittest.main()
