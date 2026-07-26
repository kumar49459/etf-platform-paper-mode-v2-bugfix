"""Historical Data Engine (Phase 1 Module 1).

Public entry point: HistoricalDataEngine. Provider abstraction, rate
limiting, symbol resolution, and storage backends are internal
implementation details — see PHASE1_Architecture_SRS.md §4, §12.6.
"""

from etf_platform.data_engine.data_engine import HistoricalDataEngine
from etf_platform.data_engine.exceptions import (
    DataEngineError,
    DataProviderError,
    SnapshotNotFoundError,
    StorageError,
    SymbolResolutionError,
)
from etf_platform.data_engine.models import (
    CorporateAction,
    CorporateActionType,
    DataSnapshot,
    InstrumentMeta,
    OHLCVBar,
    SymbolChangeEvent,
)

__all__ = [
    "HistoricalDataEngine",
    "OHLCVBar",
    "CorporateAction",
    "CorporateActionType",
    "InstrumentMeta",
    "DataSnapshot",
    "SymbolChangeEvent",
    "DataEngineError",
    "DataProviderError",
    "SymbolResolutionError",
    "SnapshotNotFoundError",
    "StorageError",
]
