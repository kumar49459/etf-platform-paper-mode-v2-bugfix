"""Core data models shared by every provider, storage backend, and the
orchestrating HistoricalDataEngine.

These mirror the conceptual schema already agreed in Phase 1 §6 (time-series
store: ohlcv, corporate_actions, etf_metadata). Frozen dataclasses: a bar
that's already been ingested and validated should not be mutable downstream —
if a correction is needed, a new record with a new `data_snapshot_id` is the
correct mechanism, not an in-place edit (this is what makes snapshots
reproducible, per Phase 1 §1.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


@dataclass(frozen=True)
class OHLCVBar:
    """One daily OHLCV bar for one symbol. Immutable — corrections create a new record in a new snapshot, never an in-place edit."""
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: float | None = None
    source: str = "unknown"

    def __post_init__(self) -> None:
        # Basic structural sanity — NOT the full Data Quality Validator pipeline
        # (that's a separate, more thorough module by design, see PHASE1 §1.3).
        # This is only enough to prevent obviously malformed objects existing
        # in memory at all, e.g. from a parsing bug in a provider adapter.
        if self.volume < 0:
            raise ValueError(f"{self.symbol} {self.trade_date}: volume cannot be negative ({self.volume})")


class CorporateActionType(str, Enum):
    """Kind of corporate action affecting a symbol's adjusted price history."""
    DIVIDEND = "dividend"
    SPLIT = "split"
    BONUS = "bonus"
    MERGER = "merger"
    OTHER = "other"


@dataclass(frozen=True)
class CorporateAction:
    """One corporate action event (dividend, split, bonus, etc.) for a symbol."""
    symbol: str
    ex_date: date
    action_type: CorporateActionType
    ratio_or_amount: float
    source: str = "unknown"


@dataclass(frozen=True)
class InstrumentMeta:
    """Metadata for one tradable instrument/ETF, as reported by a provider."""
    symbol: str
    name: str
    exchange: str
    instrument_token: int | None = None  # None until resolved via Kite instrument master
    index_tracked: str | None = None
    expense_ratio: float | None = None
    aum: float | None = None
    inception_date: date | None = None
    as_of_date: date | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class SymbolChangeEvent:
    """Emitted by the SymbolResolver when a symbol's mapping changes between
    two instrument master refreshes (e.g. relisting, ticker rename)."""

    symbol: str
    old_instrument_token: int | None
    new_instrument_token: int | None
    detected_at: datetime
    detail: str = ""


@dataclass(frozen=True)
class DataSnapshot:
    """Metadata about one immutable, named ingestion snapshot.

    Referenced by `data_snapshot_id` throughout the platform (Phase 1 §6) so
    that "which data produced this backtest / proposal" is always answerable.
    """

    snapshot_id: str
    created_at: datetime
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    source_providers: tuple[str, ...]
    row_count: int
