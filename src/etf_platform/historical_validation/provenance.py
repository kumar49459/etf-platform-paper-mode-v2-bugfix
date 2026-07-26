"""Data provenance (Milestone 5A, requirement 1). The single most
important structural rule in this whole module: every historical data
point is tagged with where it actually came from, and that tag is
propagated through every downstream computation and report. Proxy data is
never presented as ETF performance - not as a policy, as a type-level
guarantee: DataSegment.source is a required field, and every report
builder in this package groups output by source before it groups by
anything else.

TWO GAPS FOUND DURING ADVERSARIAL REVIEW (requirement 9), disclosed here
rather than silently left implicit:

1. SURVIVORSHIP BIAS: this module (and this whole package) only knows
   about symbols that exist and are chosen by the caller today. Nothing
   here accounts for ETFs that may have existed historically and were
   later delisted, merged, or closed -- a classic survivorship-bias
   source. A historical validation run using only today's five-ETF
   universe implicitly assumes that universe was always investable and
   always the right one, which is not something this module verifies or
   corrects for.

2. BENCHMARK MAPPING IS AN UNENFORCED CALLER CONVENTION: nothing in this
   module verifies that a given symbol's DataSegment.notes or the index
   series passed to tracking_difference.py actually correspond to the
   CORRECT underlying benchmark for that ETF. A caller could accidentally
   pair NIFTYBEES with the wrong index series and nothing here would
   catch it -- the correctness of that pairing is an external assumption,
   not a checked invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataSource(Enum):
    ETF_ACTUAL = "etf_actual"
    """Real, traded ETF price/volume data."""

    INDEX_PROXY = "index_proxy"
    """The underlying benchmark index's total-return series, used only
    for periods before the ETF existed. Never volume data (an index has
    no traded volume)."""

    SYNTHETIC = "synthetic"
    """Neither real ETF data nor a real index series - generated data
    used ONLY because this environment has no live data access. MUST
    NEVER be presented as historical fact in any report. Every report
    this package produces refuses to omit a loud, unmissable disclosure
    banner whenever any SYNTHETIC segment is present in the data behind
    it - see report_builder.py."""


@dataclass(frozen=True)
class DataSegment:
    symbol: str
    source: DataSource
    start_date: object
    end_date: object
    notes: str = ""

    def __post_init__(self):
        if self.end_date < self.start_date:
            raise ValueError(f"DataSegment end_date {self.end_date} precedes start_date {self.start_date}")


@dataclass(frozen=True)
class ProvenanceTimeline:
    """The ordered sequence of segments making up one symbol's full
    historical series - e.g. [INDEX_PROXY 1996-2001, ETF_ACTUAL 2001-2026]
    for NIFTYBEES. transition_dates() is what requirement 1 calls the
    "transition date" - the exact boundary where the data source changes,
    reported explicitly, never left implicit in a continuous-looking
    equity curve."""

    symbol: str
    segments: tuple

    def __post_init__(self):
        sorted_segments = sorted(self.segments, key=lambda s: s.start_date)
        if list(sorted_segments) != list(self.segments):
            raise ValueError(f"Segments for {self.symbol} must be provided in chronological order.")
        for prev, curr in zip(sorted_segments, sorted_segments[1:]):
            if curr.start_date <= prev.end_date:
                raise ValueError(
                    f"Overlapping segments for {self.symbol}: {prev.source.value} ends {prev.end_date}, "
                    f"{curr.source.value} starts {curr.start_date}."
                )

    def transition_dates(self):
        return tuple(curr.start_date for prev, curr in zip(self.segments, self.segments[1:]))

    def source_at(self, as_of):
        for segment in self.segments:
            if segment.start_date <= as_of <= segment.end_date:
                return segment.source
        return None

    def has_any_synthetic(self):
        return any(s.source == DataSource.SYNTHETIC for s in self.segments)

    def etf_only_range(self):
        etf_segments = [s for s in self.segments if s.source == DataSource.ETF_ACTUAL]
        if not etf_segments:
            return None
        return (min(s.start_date for s in etf_segments), max(s.end_date for s in etf_segments))

    def proxy_only_range(self):
        proxy_segments = [s for s in self.segments if s.source == DataSource.INDEX_PROXY]
        if not proxy_segments:
            return None
        return (min(s.start_date for s in proxy_segments), max(s.end_date for s in proxy_segments))
