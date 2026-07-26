"""Portfolio Candidate Generator (Phase 3).

Ties the ranked universe (ETFUniverseOptimizer) to your explicit
requirement: never recommend replacing an ETF without statistically
validated evidence (see stats.py for the block-bootstrap methodology).

Design decision — test only the single best-ranked peer per category, not
every higher-ranked candidate: if an incumbent has five higher-scored peers
in the same asset class, running a significance test against all five and
reporting whichever comes back significant is a multiple-comparisons
fishing expedition — at a 95% confidence level, testing 5 independent
candidates gives roughly a 23% chance of at least one false positive by
chance alone, even if none of them are actually better. Testing only the
single best-ranked peer avoids this without needing a Bonferroni correction
that would otherwise make the bar for evidence depend on how large the
universe happens to be, which is not a property a good evidence bar should
have.

Design decision — a significant, favorable result with a worse drawdown is
still reported, not suppressed: per PHASE1 §12.2, a return improvement that
comes with worse drawdown must always be surfaced as a trade-off for human
review, never silently resolved either direction by the algorithm.
"""

from __future__ import annotations

from datetime import date

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine import HistoricalDataEngine
from etf_platform.etf_optimizer import stats
from etf_platform.etf_optimizer.exceptions import InsufficientDataError
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager
from etf_platform.etf_optimizer.models import (
    CandidateGenerationReport,
    ReplacementRecommendation,
    ScreeningThresholds,
)
from etf_platform.etf_optimizer.universe_optimizer import ETFUniverseOptimizer

logger = get_logger("etf_optimizer.candidate_generator")


class PortfolioCandidateGenerator:
    def __init__(
        self,
        data_engine: HistoricalDataEngine,
        metadata_manager: ETFMetadataManager,
        thresholds: ScreeningThresholds | None = None,
        weights: dict[str, float] | None = None,
        confidence_level: float = 0.95,
    ) -> None:
        self._data_engine = data_engine
        self._metadata_manager = metadata_manager
        self._optimizer = ETFUniverseOptimizer(data_engine, metadata_manager, thresholds, weights)
        self._confidence_level = confidence_level

    def generate(
        self,
        universe_symbols: list[str],
        current_holdings: list[str],
        lookback_days: int = 365,
        snapshot_id: str | None = None,
        as_of: date | None = None,
    ) -> CandidateGenerationReport:
        as_of = as_of or date.today()
        current_holdings = sorted({s.upper() for s in current_holdings})
        # Current holdings must be scored on equal footing with the rest of
        # the universe, or they can never be objectively out-ranked.
        full_universe = sorted(set(universe_symbols) | set(current_holdings))

        universe_report = self._optimizer.optimize(
            full_universe, lookback_days=lookback_days, current_holdings=current_holdings,
            snapshot_id=snapshot_id, as_of=as_of,
        )

        universe_metadata = self._metadata_manager.get_universe_metadata(full_universe)
        holdings_metadata = {s: universe_metadata[s] for s in current_holdings}
        recommendations: list[ReplacementRecommendation] = []
        insufficient_data: list[str] = []

        from datetime import timedelta
        start = as_of - timedelta(days=lookback_days)
        bars_cache = self._data_engine.get_ohlcv(full_universe, start, as_of, snapshot_id=snapshot_id)

        for incumbent_symbol in current_holdings:
            incumbent_score = universe_report.get_score(incumbent_symbol)
            incumbent_meta = holdings_metadata.get(incumbent_symbol)

            if incumbent_score is None:
                logger.info(
                    "'%s' did not pass screening (or has no scored data) — cannot evaluate replacements.",
                    incumbent_symbol,
                )
                insufficient_data.append(incumbent_symbol)
                continue
            if incumbent_meta is None or incumbent_meta.asset_class is None:
                logger.info(
                    "'%s' has no known asset_class — cannot identify comparable peer candidates.",
                    incumbent_symbol,
                )
                insufficient_data.append(incumbent_symbol)
                continue

            peers = [
                score for score in universe_report.ranked_scores
                if score.symbol != incumbent_symbol
                and score.rank < incumbent_score.rank
                and universe_metadata.get(score.symbol) is not None
                and universe_metadata[score.symbol].asset_class == incumbent_meta.asset_class
            ]
            if not peers:
                continue  # nothing outranks this incumbent within its own category — no evidence to test

            best_peer = min(peers, key=lambda s: s.rank)

            try:
                test_result = stats.validate_replacement(
                    candidate_symbol=best_peer.symbol,
                    incumbent_symbol=incumbent_symbol,
                    candidate_bars=bars_cache.get(best_peer.symbol, []),
                    incumbent_bars=bars_cache.get(incumbent_symbol, []),
                    confidence_level=self._confidence_level,
                )
            except InsufficientDataError as exc:
                logger.info("Cannot validate %s -> %s: %s", incumbent_symbol, best_peer.symbol, exc)
                insufficient_data.append(incumbent_symbol)
                continue

            if not test_result.favors_candidate:
                logger.info(
                    "No statistically validated evidence to replace %s with %s (best peer): "
                    "CI=[%.4f, %.4f] at %.0f%% confidence.",
                    incumbent_symbol, best_peer.symbol, test_result.ci_low, test_result.ci_high,
                    self._confidence_level * 100,
                )
                continue

            drawdown_note = ""
            if test_result.drawdown_worse:
                drawdown_note = (
                    f"CAUTION: {best_peer.symbol}'s historical max drawdown "
                    f"({test_result.candidate_max_drawdown:.1%}) exceeds {incumbent_symbol}'s "
                    f"({test_result.incumbent_max_drawdown:.1%}). Return improvement is statistically "
                    "validated, but this trade-off requires your manual review before acting on it "
                    "(per the platform's capital-preservation priority) — this is not an automatic "
                    "recommendation to proceed."
                )

            recommendations.append(
                ReplacementRecommendation(
                    incumbent_symbol=incumbent_symbol,
                    candidate_symbol=best_peer.symbol,
                    rationale=(
                        f"{best_peer.symbol} ranks #{best_peer.rank} vs {incumbent_symbol}'s #{incumbent_score.rank} "
                        f"in the same asset class ('{incumbent_meta.asset_class}'), with a statistically significant "
                        f"annualized return advantage of {test_result.observed_diff:.2%} "
                        f"(95% CI: [{test_result.ci_low:.2%}, {test_result.ci_high:.2%}], "
                        f"n={test_result.n_observations} trading days)."
                    ),
                    test_result=test_result,
                    drawdown_tradeoff_note=drawdown_note,
                )
            )

        logger.info(
            "Candidate generation complete: %d holdings evaluated, %d validated recommendations, "
            "%d with insufficient data.",
            len(current_holdings), len(recommendations), len(insufficient_data),
        )

        return CandidateGenerationReport(
            generated_at=as_of,
            current_holdings=tuple(current_holdings),
            universe_report=universe_report,
            replacement_recommendations=tuple(recommendations),
            holdings_with_insufficient_data=tuple(insufficient_data),
        )
