"""Time-series storage backends (CSV default, Parquet when pyarrow is
available) plus the SQLite-backed snapshot registry.

See `factory.py` for how a backend is selected from config, and
`PHASE1_Architecture_SRS.md` §6/§13.2 for the storage design this implements.
"""

from etf_platform.data_engine.storage.factory import build_timeseries_store
from etf_platform.data_engine.storage.snapshot_registry import SnapshotRegistry

__all__ = ["build_timeseries_store", "SnapshotRegistry"]
