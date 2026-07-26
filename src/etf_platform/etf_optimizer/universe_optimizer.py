"""ETF Universe Optimizer (Phase 3) — orchestrates ETFMetadataManager,
UniverseScreeningEngine, and ETFScorer into one explainable ranking of a
given universe of symbols.

Deliberately takes an explicit `universe_symbols` list rather than
auto-discovering "the complete Indian ETF universe" from a hardcoded
source: NSE lists ETFs under the equity segment mixed with regular stocks,
and there's no single authoritative "this NSE symbol is an ETF" flag
available from Phase 2's Data Engine without additional classification
logic this phase doesn't build. Per Phase 3's requirement to "support the
complete Indian ETF universe," the design supports scoring an arbitrarily
large `universe_symbols` list — the caller is responsible for supplying
that list (e.g. from AMFI's ETF listing, or a maintained symbol file), which
keeps this class correct today and trivially extensible once a full-universe
symbol source is wired up, rather than silently scoring a wrong or
incomplete "complete universe."
"""

from __future__ import annotations

from datetime import date, timedelta

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine import HistoricalDataEngine
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager
from etf_platform.etf_optimizer.models import (
    ScreeningStatus,
    ScreeningThresholds,
    UniverseOptimizationReport,
)
from etf_platform.etf_optimizer.scoring import ETFScorer

logger = get_logger("etf_optimizer.universe_optimizer")


class ETFUniverseOptimizer:
    def __init__(
        self,
        data_engine: HistoricalDataEngine,
        metadata_manager: ETFMetadataManager,
        thresholds: ScreeningThresholds | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        from etf_platform.etf_optimizer.screening_engine import UniverseScreeningEngine

        self._data_engine = data_engine
        self._metadata_manager = metadata_manager
        self._screening_engine = UniverseScreeningEngine(thresholds or ScreeningThresholds())
        self._scorer = ETFScorer(weights)

    def optimize(
        self,
        universe_symbols: list[str],
        lookback_days: int = 365,
        current_holdings: list[str] | None = None,
        snapshot_id: str | None = None,
        as_of: date | None = None,
    ) -> UniverseOptimizationReport:
        as_of = as_of or date.today()
        start = as_of - timedelta(days=lookback_days)
        current_holdings = current_holdings or []

        universe_symbols = sorted({s.upper() for s in universe_symbols})
        metadata = self._metadata_manager.get_universe_metadata(universe_symbols)
        bars_by_symbol = self._data_engine.get_ohlcv(universe_symbols, start, as_of, snapshot_id=snapshot_id)

        screening_results = [
            self._screening_engine.screen(symbol, metadata[symbol], bars_by_symbol.get(symbol, []))
            for symbol in universe_symbols
        ]

        passed_symbols = [r.symbol for r in screening_results if r.overall_status == ScreeningStatus.PASS]
        excluded_symbols = [r.symbol for r in screening_results if r.overall_status != ScreeningStatus.PASS]

        current_holdings_bars = (
            self._data_engine.get_ohlcv(current_holdings, start, as_of, snapshot_id=snapshot_id)
            if current_holdings
            else {}
        )
        current_holdings_metadata = self._metadata_manager.get_universe_metadata(current_holdings)

        candidate_bars = {s: bars_by_symbol.get(s, []) for s in passed_symbols}
        candidate_metadata = {s: metadata[s] for s in passed_symbols}
        ranked_scores = self._scorer.score_universe(
            candidate_bars, candidate_metadata, current_holdings_bars, current_holdings_metadata
        )

        logger.info(
            "Universe optimization complete: %d evaluated, %d passed screening, %d excluded.",
            len(universe_symbols), len(passed_symbols), len(excluded_symbols),
        )

        return UniverseOptimizationReport(
            generated_at=as_of,
            screening_results=tuple(screening_results),
            ranked_scores=tuple(ranked_scores),
            excluded_symbols=tuple(excluded_symbols),
        )
