"""Tests for the Historical Data Acquisition Module: CSVDataProvider,
IndexProxyDataProvider, ValidatedDataProvider, HistoricalDataAcquisitionService.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.backtesting.models import BacktestConfig
from etf_platform.data_engine.exceptions import DataProviderError
from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.historical_validation import (
    CSVDataProvider,
    HistoricalDataAcquisitionService,
    IndexProxyDataProvider,
    NoProviderForRangeError,
    ValidatedDataProvider,
)
from etf_platform.historical_validation.provenance import DataSource
from etf_platform.historical_validation.reproducibility_manifest import DataIntegrityAbortedError
from etf_platform.strategy_engine import StrategyEngine


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("date,open,high,low,close,volume\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


class TestCSVDataProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_implements_frozen_dataprovider_interface(self):
        provider = CSVDataProvider(self.tmp_dir)
        self.assertIsInstance(provider, DataProvider)

    def test_loads_bars_within_range(self):
        write_csv(self.tmp_dir / "A.csv", [
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, 101.5, 55000),
            ("2024-01-03", 101.5, 103, 101, 102.5, 48000),
        ])
        provider = CSVDataProvider(self.tmp_dir)
        bars = provider.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 2))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].close, 100.5)

    def test_source_field_set_to_provider_name(self):
        write_csv(self.tmp_dir / "A.csv", [("2024-01-01", 100, 101, 99, 100.5, 50000)])
        provider = CSVDataProvider(self.tmp_dir, provider_name="my_source")
        bars = provider.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 1))
        self.assertEqual(bars[0].source, "my_source")

    def test_missing_file_raises_data_provider_error(self):
        provider = CSVDataProvider(self.tmp_dir)
        with self.assertRaises(DataProviderError):
            provider.fetch_ohlcv("MISSING", date(2024, 1, 1), date(2024, 1, 2))

    def test_missing_required_column_raises(self):
        (self.tmp_dir / "BAD.csv").write_text("date,close\n2024-01-01,100\n")
        provider = CSVDataProvider(self.tmp_dir)
        with self.assertRaises(DataProviderError):
            provider.fetch_ohlcv("BAD", date(2024, 1, 1), date(2024, 1, 2))

    def test_malformed_row_raises(self):
        (self.tmp_dir / "BAD2.csv").write_text(
            "date,open,high,low,close,volume\n2024-01-01,not_a_number,101,99,100,50000\n"
        )
        provider = CSVDataProvider(self.tmp_dir)
        with self.assertRaises(DataProviderError):
            provider.fetch_ohlcv("BAD2", date(2024, 1, 1), date(2024, 1, 2))

    def test_bars_returned_in_chronological_order(self):
        write_csv(self.tmp_dir / "A.csv", [
            ("2024-01-03", 101.5, 103, 101, 102.5, 48000),
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, 101.5, 55000),
        ])
        provider = CSVDataProvider(self.tmp_dir)
        bars = provider.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 3))
        dates = [b.trade_date for b in bars]
        self.assertEqual(dates, sorted(dates))


class TestBacktestEngineIndependenceFromSource(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_backtest_engine_consumes_csv_sourced_data_with_zero_changes(self):
        write_csv(self.tmp_dir / "A.csv", [
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, 101.5, 55000),
            ("2024-01-03", 101.5, 103, 101, 102.5, 48000),
            ("2024-01-04", 102.5, 103.5, 102, 103.0, 52000),
        ])
        provider = CSVDataProvider(self.tmp_dir)
        price_history = {"A": provider.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 4))}
        strategy = StrategyEngine({"A": 1.0})
        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 1, 4), initial_capital=10000, symbols=("A",))
        engine = BacktestEngine(config, strategy)
        result = engine.run(price_history)
        self.assertGreater(len(result.trades), 0)


class TestIndexProxyDataProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_relabels_source_as_proxy(self):
        write_csv(self.tmp_dir / "A.csv", [("2024-01-01", 100, 101, 99, 100.5, 50000)])
        wrapped = CSVDataProvider(self.tmp_dir, provider_name="index_source")
        proxy = IndexProxyDataProvider(wrapped)
        bars = proxy.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 1))
        self.assertIn("index_proxy", bars[0].source)

    def test_name_reflects_wrapping(self):
        wrapped = CSVDataProvider(self.tmp_dir, provider_name="foo")
        proxy = IndexProxyDataProvider(wrapped)
        self.assertIn("foo", proxy.name)
        self.assertIn("index_proxy", proxy.name)


class TestValidatedDataProvider(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_valid_data_passes_through(self):
        write_csv(self.tmp_dir / "A.csv", [
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, 101.5, 55000),
        ])
        wrapped = CSVDataProvider(self.tmp_dir)
        validated = ValidatedDataProvider(wrapped)
        bars = validated.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 2))
        self.assertEqual(len(bars), 2)

    def test_invalid_data_aborts(self):
        write_csv(self.tmp_dir / "A.csv", [
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, -999.0, 55000),
        ])
        wrapped = CSVDataProvider(self.tmp_dir)
        validated = ValidatedDataProvider(wrapped)
        with self.assertRaises(DataIntegrityAbortedError):
            validated.fetch_ohlcv("A", date(2024, 1, 1), date(2024, 1, 2))


class TestHistoricalDataAcquisitionService(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        write_csv(self.tmp_dir / "etf" / "A.csv", [
            ("2024-01-01", 100, 101, 99, 100.5, 50000),
            ("2024-01-02", 100.5, 102, 100, 101.5, 55000),
        ])
        write_csv(self.tmp_dir / "proxy" / "A.csv", [
            ("2023-12-01", 95, 96, 94, 95.5, 10000),
            ("2023-12-02", 95.5, 96.5, 95, 96.0, 11000),
        ])
        self.etf_provider = CSVDataProvider(self.tmp_dir / "etf")
        self.proxy_provider = IndexProxyDataProvider(CSVDataProvider(self.tmp_dir / "proxy", provider_name="idx"))

    def test_no_registration_raises_explicit_error(self):
        service = HistoricalDataAcquisitionService()
        with self.assertRaises(NoProviderForRangeError):
            service.fetch("UNREGISTERED")

    def test_combines_proxy_and_etf_segments_chronologically(self):
        service = HistoricalDataAcquisitionService()
        service.register("A", date(2023, 12, 1), date(2023, 12, 2), self.proxy_provider, DataSource.INDEX_PROXY)
        service.register("A", date(2024, 1, 1), date(2024, 1, 2), self.etf_provider, DataSource.ETF_ACTUAL)
        bars, timeline = service.fetch("A")
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars[0].trade_date, date(2023, 12, 1))
        self.assertEqual(bars[-1].trade_date, date(2024, 1, 2))

    def test_provenance_timeline_correctly_built(self):
        service = HistoricalDataAcquisitionService()
        service.register("A", date(2023, 12, 1), date(2023, 12, 2), self.proxy_provider, DataSource.INDEX_PROXY)
        service.register("A", date(2024, 1, 1), date(2024, 1, 2), self.etf_provider, DataSource.ETF_ACTUAL)
        _, timeline = service.fetch("A")
        self.assertEqual(timeline.transition_dates(), (date(2024, 1, 1),))
        self.assertEqual(timeline.etf_only_range(), (date(2024, 1, 1), date(2024, 1, 2)))
        self.assertEqual(timeline.proxy_only_range(), (date(2023, 12, 1), date(2023, 12, 2)))

    def test_overlap_rejected_at_register_time_before_any_fetch(self):
        service = HistoricalDataAcquisitionService()
        service.register("A", date(2023, 1, 1), date(2023, 6, 1), self.etf_provider, DataSource.ETF_ACTUAL)
        with self.assertRaises(ValueError):
            service.register("A", date(2023, 3, 1), date(2023, 9, 1), self.etf_provider, DataSource.ETF_ACTUAL)

    def test_fetch_all_produces_backtest_engine_compatible_shape(self):
        service = HistoricalDataAcquisitionService()
        service.register("A", date(2024, 1, 1), date(2024, 1, 2), self.etf_provider, DataSource.ETF_ACTUAL)
        bars_by_symbol, timelines = service.fetch_all(["A"])
        self.assertIsInstance(bars_by_symbol, dict)
        self.assertIsInstance(bars_by_symbol["A"], list)
        self.assertEqual(len(timelines), 1)


if __name__ == "__main__":
    unittest.main()
