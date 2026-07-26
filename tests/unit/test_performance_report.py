"""Integration tests for build_performance_report, particularly benchmark
comparison correctness."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.performance_analytics.report import build_performance_report


class TestBenchmarkComparison(unittest.TestCase):
    def test_no_benchmark_gives_none(self) -> None:
        curve = [(date(2025, 1, 1) + timedelta(days=i), 100000 * 1.001**i) for i in range(100)]
        report = build_performance_report(curve, realized_trade_pnls=[])
        self.assertIsNone(report.benchmark)

    def test_outperformance_gives_positive_excess_return(self) -> None:
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)]
        strategy_curve = [(d, 100000 * 1.002**i) for i, d in enumerate(dates)]
        benchmark_curve = [(d, 100000 * 1.001**i) for i, d in enumerate(dates)]
        report = build_performance_report(strategy_curve, [], benchmark_curve, "NIFTYBEES")
        self.assertIsNotNone(report.benchmark)
        self.assertGreater(report.benchmark.excess_return, 0)

    def test_underperformance_gives_negative_excess_return(self) -> None:
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)]
        strategy_curve = [(d, 100000 * 1.0005**i) for i, d in enumerate(dates)]
        benchmark_curve = [(d, 100000 * 1.002**i) for i, d in enumerate(dates)]
        report = build_performance_report(strategy_curve, [], benchmark_curve, "NIFTYBEES")
        self.assertLess(report.benchmark.excess_return, 0)

    def test_identical_curves_give_perfect_correlation(self) -> None:
        dates = [date(2025, 1, 1) + timedelta(days=i) for i in range(100)]
        curve = [(d, 100000 * 1.001**i) for i, d in enumerate(dates)]
        report = build_performance_report(curve, [], curve, "SELF")
        self.assertAlmostEqual(report.benchmark.correlation, 1.0, places=4)
        self.assertAlmostEqual(report.benchmark.tracking_error_annualized, 0.0, places=6)
        self.assertAlmostEqual(report.benchmark.excess_return, 0.0, places=6)

    def test_no_common_dates_gives_none(self) -> None:
        strategy_curve = [(date(2025, 1, 1) + timedelta(days=i), 100000.0) for i in range(10)]
        benchmark_curve = [(date(2026, 1, 1) + timedelta(days=i), 100000.0) for i in range(10)]
        report = build_performance_report(strategy_curve, [], benchmark_curve, "X")
        self.assertIsNone(report.benchmark)


class TestSummaryDict(unittest.TestCase):
    def test_summary_dict_json_serializable(self) -> None:
        import json

        curve = [(date(2025, 1, 1) + timedelta(days=i), 100000 * 1.0005**i) for i in range(200)]
        report = build_performance_report(curve, [100, -50, 200])
        serialized = json.dumps(report.summary_dict())
        self.assertIn("xirr", serialized)


if __name__ == "__main__":
    unittest.main()
