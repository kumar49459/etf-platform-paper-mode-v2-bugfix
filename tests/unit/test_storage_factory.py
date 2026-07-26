"""Unit tests for the storage backend factory (auto/csv/parquet selection)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from etf_platform.data_engine.exceptions import StorageError
from etf_platform.data_engine.storage.csv_store import CSVTimeSeriesStore
from etf_platform.data_engine.storage.factory import build_timeseries_store


class TestStorageFactory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_explicit_csv_backend(self) -> None:
        store = build_timeseries_store("csv", self.tmp_dir)
        self.assertIsInstance(store, CSVTimeSeriesStore)

    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(StorageError):
            build_timeseries_store("not_a_real_backend", self.tmp_dir)

    def test_auto_resolves_to_csv_when_pyarrow_unavailable(self) -> None:
        with mock.patch("etf_platform.data_engine.storage.factory._pyarrow_available", return_value=False):
            store = build_timeseries_store("auto", self.tmp_dir)
        self.assertIsInstance(store, CSVTimeSeriesStore)

    def test_auto_resolves_to_parquet_when_pyarrow_available(self) -> None:
        # We don't require pyarrow to actually be installed to test the
        # *selection logic* — only ParquetTimeSeriesStore's own constructor
        # needs real pyarrow, which is exercised separately (and explicitly
        # not covered in this sandbox — see parquet_store.py docstring).
        # Here we just confirm 'auto' would route to 'parquet' when the
        # availability check says yes, by patching the constructor it would
        # call.
        with mock.patch("etf_platform.data_engine.storage.factory._pyarrow_available", return_value=True):
            with mock.patch(
                "etf_platform.data_engine.storage.parquet_store.ParquetTimeSeriesStore"
            ) as mock_parquet_cls:
                build_timeseries_store("auto", self.tmp_dir)
                mock_parquet_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
