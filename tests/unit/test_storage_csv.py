"""Unit tests for CSVTimeSeriesStore (the tested-by-default backend)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar
from etf_platform.data_engine.storage.csv_store import CSVTimeSeriesStore


class TestCSVTimeSeriesStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.store = CSVTimeSeriesStore(self.tmp_dir)

    def test_write_then_read_ohlcv_roundtrip(self) -> None:
        bars = [
            OHLCVBar("NIFTYBEES", date(2026, 1, 2), 100, 102, 99, 101, 5000, 101.0, "nse"),
            OHLCVBar("NIFTYBEES", date(2026, 1, 3), 101, 103, 100, 102, 6000, 102.0, "nse"),
        ]
        self.store.write_ohlcv("snap-1", "NIFTYBEES", bars)
        read_back = self.store.read_ohlcv("snap-1", "NIFTYBEES", date(2026, 1, 1), date(2026, 1, 5))
        self.assertEqual(len(read_back), 2)
        self.assertEqual(read_back[0].close, 101.0)
        self.assertEqual(read_back[1].volume, 6000)

    def test_read_filters_by_date_range(self) -> None:
        bars = [OHLCVBar("X", date(2026, 1, d), 1, 1, 1, 1, 1) for d in (2, 3, 4)]
        self.store.write_ohlcv("snap-1", "X", bars)
        read_back = self.store.read_ohlcv("snap-1", "X", date(2026, 1, 3), date(2026, 1, 3))
        self.assertEqual(len(read_back), 1)
        self.assertEqual(read_back[0].trade_date, date(2026, 1, 3))

    def test_read_nonexistent_snapshot_returns_empty(self) -> None:
        result = self.store.read_ohlcv("nonexistent", "X", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(result, [])

    def test_snapshots_are_isolated(self) -> None:
        bars_a = [OHLCVBar("X", date(2026, 1, 2), 1, 1, 1, 1, 1)]
        bars_b = [OHLCVBar("X", date(2026, 1, 2), 2, 2, 2, 2, 2)]
        self.store.write_ohlcv("snap-a", "X", bars_a)
        self.store.write_ohlcv("snap-b", "X", bars_b)
        read_a = self.store.read_ohlcv("snap-a", "X", date(2026, 1, 1), date(2026, 1, 3))
        read_b = self.store.read_ohlcv("snap-b", "X", date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual(read_a[0].open, 1)
        self.assertEqual(read_b[0].open, 2)

    def test_corporate_actions_roundtrip(self) -> None:
        actions = [
            CorporateAction("X", date(2026, 2, 1), CorporateActionType.DIVIDEND, 2.5, "nse"),
            CorporateAction("X", date(2026, 3, 1), CorporateActionType.SPLIT, 2.0, "nse"),
        ]
        self.store.write_corporate_actions("snap-1", "X", actions)
        read_back = self.store.read_corporate_actions("snap-1", "X")
        self.assertEqual(len(read_back), 2)
        self.assertEqual(read_back[0].action_type, CorporateActionType.DIVIDEND)
        self.assertEqual(read_back[1].ratio_or_amount, 2.0)

    def test_adjusted_close_none_roundtrips_as_none(self) -> None:
        bars = [OHLCVBar("X", date(2026, 1, 2), 1, 1, 1, 1, 1, adjusted_close=None)]
        self.store.write_ohlcv("snap-1", "X", bars)
        read_back = self.store.read_ohlcv("snap-1", "X", date(2026, 1, 1), date(2026, 1, 3))
        self.assertIsNone(read_back[0].adjusted_close)


if __name__ == "__main__":
    unittest.main()
