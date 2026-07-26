"""Abstract TimeSeriesStore interface.

Every snapshot is stored as an isolated, immutable subtree:
    <base_path>/<snapshot_id>/ohlcv/<symbol>.<ext>
    <base_path>/<snapshot_id>/corporate_actions/<symbol>.<ext>

Design decision: snapshot-scoped subdirectories rather than one
ever-growing partitioned file with an appended snapshot_id column.
Rationale: Phase 1 §1.4 requires snapshots to be immutable and independently
reproducible — an isolated subtree makes "delete/archive an old snapshot" a
plain filesystem operation, and makes it structurally impossible for a bug
to accidentally mix rows from two snapshots in one read, which a shared
append-only file with a filter column would not guarantee as strongly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from etf_platform.data_engine.models import CorporateAction, OHLCVBar


class TimeSeriesStore(ABC):
    """Abstract interface for persisting/reading OHLCV and corporate-action data within an immutable, snapshot-scoped subtree. Implemented by CSVTimeSeriesStore (default) and ParquetTimeSeriesStore."""
    @abstractmethod
    def write_ohlcv(self, snapshot_id: str, symbol: str, bars: list[OHLCVBar]) -> None:
        ...

    @abstractmethod
    def read_ohlcv(self, snapshot_id: str, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        ...

    @abstractmethod
    def write_corporate_actions(self, snapshot_id: str, symbol: str, actions: list[CorporateAction]) -> None:
        ...

    @abstractmethod
    def read_corporate_actions(self, snapshot_id: str, symbol: str) -> list[CorporateAction]:
        ...
