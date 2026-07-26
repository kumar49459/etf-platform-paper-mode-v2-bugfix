"""Unit tests for etf_optimizer.price_metrics."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import price_metrics


def bar(d: date, close: float, volume: int = 1000, high=None, low=None) -> OHLCVBar:
    return OHLCVBar(
        symbol="X", trade_date=d, open=close, high=high or close + 1, low=low or close - 1,
        close=close, volume=volume,
    )


def bars_from_closes(closes: list[float], start: date = date(2026, 1, 1)) -> list[OHLCVBar]:
    return [bar(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class TestDailyReturns(unittest.TestCase):
    def test_empty_for_fewer_than_2_bars(self) -> None:
        self.assertEqual(len(price_metrics.daily_returns([bar(date(2026, 1, 1), 100)])), 0)

    def test_computes_simple_returns(self) -> None:
        bars = bars_from_closes([100, 110, 99])
        returns = price_metrics.daily_returns(bars)
        self.assertAlmostEqual(returns[0], 0.10, places=6)
        self.assertAlmostEqual(returns[1], -0.1, places=6)


class TestAnnualizedVolatility(unittest.TestCase):
    def test_none_for_insufficient_data(self) -> None:
        self.assertIsNone(price_metrics.annualized_volatility([bar(date(2026, 1, 1), 100)]))

    def test_zero_volatility_for_constant_prices(self) -> None:
        bars = bars_from_closes([100] * 10)
        vol = price_metrics.annualized_volatility(bars)
        self.assertAlmostEqual(vol, 0.0, places=6)

    def test_positive_for_varying_prices(self) -> None:
        bars = bars_from_closes([100, 105, 95, 110, 90, 108])
        vol = price_metrics.annualized_volatility(bars)
        self.assertGreater(vol, 0)


class TestTurnoverAndVolume(unittest.TestCase):
    def test_average_turnover(self) -> None:
        bars = [bar(date(2026, 1, 1), 100, volume=1000), bar(date(2026, 1, 2), 200, volume=2000)]
        # turnovers: 100*1000=100000, 200*2000=400000 -> avg 250000
        self.assertAlmostEqual(price_metrics.average_daily_turnover_inr(bars), 250000.0)

    def test_average_volume(self) -> None:
        bars = [bar(date(2026, 1, 1), 100, volume=1000), bar(date(2026, 1, 2), 100, volume=3000)]
        self.assertAlmostEqual(price_metrics.average_daily_volume(bars), 2000.0)

    def test_none_for_empty_bars(self) -> None:
        self.assertIsNone(price_metrics.average_daily_turnover_inr([]))
        self.assertIsNone(price_metrics.average_daily_volume([]))


class TestMaxDrawdown(unittest.TestCase):
    def test_none_for_insufficient_data(self) -> None:
        self.assertIsNone(price_metrics.max_drawdown([bar(date(2026, 1, 1), 100)]))

    def test_computes_correct_drawdown(self) -> None:
        bars = bars_from_closes([100, 120, 90, 110])  # peak 120, trough 90 -> dd = 30/120 = 0.25
        dd = price_metrics.max_drawdown(bars)
        self.assertAlmostEqual(dd, 0.25, places=6)

    def test_monotonic_increase_has_zero_drawdown(self) -> None:
        bars = bars_from_closes([100, 105, 110, 120])
        self.assertAlmostEqual(price_metrics.max_drawdown(bars), 0.0)


class TestReturnCorrelation(unittest.TestCase):
    def test_perfectly_correlated_series(self) -> None:
        bars_a = bars_from_closes([100, 110, 121, 108.9])
        bars_b = bars_from_closes([50, 55, 60.5, 54.45])  # identical % moves
        corr = price_metrics.return_correlation(bars_a, bars_b)
        self.assertAlmostEqual(corr, 1.0, places=4)

    def test_inversely_correlated_series(self) -> None:
        bars_a = bars_from_closes([100, 110, 100, 115])
        bars_b = bars_from_closes([100, 90, 100, 85])
        corr = price_metrics.return_correlation(bars_a, bars_b)
        self.assertLess(corr, 0)

    def test_none_for_no_overlap(self) -> None:
        bars_a = bars_from_closes([100, 110], start=date(2026, 1, 1))
        bars_b = bars_from_closes([100, 110], start=date(2027, 1, 1))
        self.assertIsNone(price_metrics.return_correlation(bars_a, bars_b))

    def test_none_for_constant_series(self) -> None:
        bars_a = bars_from_closes([100, 100, 100, 100])
        bars_b = bars_from_closes([50, 55, 52, 58])
        self.assertIsNone(price_metrics.return_correlation(bars_a, bars_b))


class TestAlignedReturns(unittest.TestCase):
    def test_aligns_on_common_dates_only(self) -> None:
        bars_a = bars_from_closes([100, 110, 120], start=date(2026, 1, 1))
        bars_b = bars_from_closes([50, 55], start=date(2026, 1, 1))  # shorter series
        returns_a, returns_b = price_metrics.aligned_returns(bars_a, bars_b)
        self.assertEqual(len(returns_a), len(returns_b))
        self.assertEqual(len(returns_a), 1)  # only 1 common return-pair possible


if __name__ == "__main__":
    unittest.main()
