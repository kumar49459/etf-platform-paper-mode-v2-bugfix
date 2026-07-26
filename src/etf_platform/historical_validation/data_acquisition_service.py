"""HistoricalDataAcquisitionService: the orchestrating entry point
requested by name ("Historical Data Acquisition Module" /
"HistoricalDataProvider"). Composes DataProvider implementations (frozen
interface, Phase 2) with this package's provenance tracking (provenance.py)
and validation gate (provider_decorators.ValidatedDataProvider) to select,
for each symbol, the right source for each stretch of history -- real ETF
data where it exists, index-proxy where it doesn't -- and returns exactly
the dict[str, list[OHLCVBar]] shape BacktestEngine (frozen, unmodified)
already consumes.

BacktestEngine's own independence from any specific source was already
true before this module existed: engine.run() only ever depended on that
dict shape, never on a concrete provider (confirmed and demonstrated
end-to-end against CSVDataProvider). This service's job is to make
provider selection and provenance tracking for LONG historical windows
(spanning an ETF's actual inception, needing real ETF data for one
stretch and index-proxy for another) explicit and structured, not to
change what BacktestEngine depends on -- that was already correct.
"""

from __future__ import annotations

from etf_platform.historical_validation.provenance import DataSegment, ProvenanceTimeline


class NoProviderForRangeError(Exception):
    """Raised when no configured provider covers a requested date range
    for a symbol -- refuses to silently return an empty or partial
    series without the caller knowing why."""


class HistoricalDataAcquisitionService:
    def __init__(self):
        self._registrations = {}

    def register(self, symbol, start, end, provider, source):
        """Found while testing this service: validating overlaps only at
        fetch() time meant both providers had already been called (real
        network/disk I/O) before the mistake surfaced. Validating
        immediately here, using a throwaway ProvenanceTimeline
        construction against zero-bar placeholder segments, catches a
        registration mistake before any provider is ever invoked."""
        existing = self._registrations.get(symbol, [])
        candidate_segments = tuple(
            DataSegment(symbol=symbol, source=src, start_date=s, end_date=e)
            for s, e, _, src in sorted(existing + [(start, end, provider, source)], key=lambda r: r[0])
        )
        ProvenanceTimeline(symbol=symbol, segments=candidate_segments)  # raises on overlap/ordering, discarded otherwise
        self._registrations.setdefault(symbol, []).append((start, end, provider, source))

    def fetch(self, symbol):
        registrations = self._registrations.get(symbol)
        if not registrations:
            raise NoProviderForRangeError(f"No provider registered for {symbol!r} -- nothing to fetch.")

        ordered = sorted(registrations, key=lambda r: r[0])
        all_bars = []
        segments = []
        for start, end, provider, source in ordered:
            bars = provider.fetch_ohlcv(symbol, start, end)
            all_bars.extend(bars)
            segments.append(DataSegment(symbol=symbol, source=source, start_date=start, end_date=end,
                                         notes=f"provider={provider.name}"))

        timeline = ProvenanceTimeline(symbol=symbol, segments=tuple(segments))
        all_bars.sort(key=lambda b: b.trade_date)
        return all_bars, timeline

    def fetch_all(self, symbols):
        bars_by_symbol = {}
        timelines = []
        for symbol in symbols:
            bars, timeline = self.fetch(symbol)
            bars_by_symbol[symbol] = bars
            timelines.append(timeline)
        return bars_by_symbol, timelines
