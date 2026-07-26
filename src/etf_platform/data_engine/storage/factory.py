"""Selects a TimeSeriesStore implementation based on config.

`storage_backend: auto` (the default) picks Parquet if `pyarrow` is
importable, otherwise falls back to CSV — this means the exact same config
file works unmodified on a minimal environment and a fully-provisioned one,
which matters given Phase 1's split between a dependency-light live
instance and a fuller research instance (§12.1).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.exceptions import StorageError
from etf_platform.data_engine.storage.base import TimeSeriesStore
from etf_platform.data_engine.storage.csv_store import CSVTimeSeriesStore

logger = get_logger("data_engine.storage.factory")


def _pyarrow_available() -> bool:
    return importlib.util.find_spec("pyarrow") is not None


def build_timeseries_store(storage_backend: str, base_path: str | Path) -> TimeSeriesStore:
    backend = storage_backend
    if backend == "auto":
        backend = "parquet" if _pyarrow_available() else "csv"
        logger.info("storage_backend='auto' resolved to '%s'", backend)

    if backend == "csv":
        return CSVTimeSeriesStore(base_path)
    if backend == "parquet":
        from etf_platform.data_engine.storage.parquet_store import ParquetTimeSeriesStore

        return ParquetTimeSeriesStore(base_path)

    raise StorageError(f"Unknown storage_backend '{storage_backend}'")
