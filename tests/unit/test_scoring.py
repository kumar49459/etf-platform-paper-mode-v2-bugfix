"""Unit tests for ETFScorer — the 8-dimension composite scoring engine."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFMetadata
from etf_platform.etf_optimizer.scoring import DEFAULT_WEIGHTS, ETFScorer


def bars_from_closes(closes: list[float], volume: int = 100000, start: date = date(2025, 1, 1)) -> list[OHLCVBar]:
    return [
        OHLCVBar("X", start + timedelta(days=i), c, c + 1, c - 1, c, volume)
        for i, c in enumerate(closes)
    ]


def meta(symbol: str, **overrides) -> ETFMetadata:
    defaults = dict(symbol=symbol, name=symbol, exchange="NSE")
    defaults.update(overrides)
    return ETFMetadata(**defaults)


class TestDefaultWeights(unittest.TestCase):
    def test_default_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(DEFAULT_WEIGHTS.values()), 1.0, places=6)

    def test_default_weights_equal(self) -> None:
        values = set(DEFAULT_WEIGHTS.values())
        self.assertEqual(len(values), 1)  # all identical -> equal weighting


class TestScorerConstruction(unittest.TestCase):
    def test_unknown_metric_in_weights_raises(self) -> None:
        with self.assertRaises(ValueError):
            ETFScorer(weights={"not_a_real_metric": 1.0})


class TestScoreUniverseBasics(unittest.TestCase):
    def test_higher_liquidity_scores_higher_all_else_equal(self) -> None:
        scorer = ETFScorer()
        # Two ETFs with identical price series but different volume -> different liquidity.
        closes = [100 + i * 0.1 for i in range(100)]
        candidates = {
            "HIGH_LIQ": bars_from_closes(closes, volume=1_000_000),
            "LOW_LIQ": bars_from_closes(closes, volume=1_000),
        }
        metadata = {"HIGH_LIQ": meta("HIGH_LIQ"), "LOW_LIQ": meta("LOW_LIQ")}
        scores = scorer.score_universe(candidates, metadata)
        by_symbol = {s.symbol: s for s in scores}
        self.assertGreater(by_symbol["HIGH_LIQ"].composite_score, by_symbol["LOW_LIQ"].composite_score)

    def test_lower_expense_ratio_scores_higher(self) -> None:
        scorer = ETFScorer()
        closes = [100] * 100
        candidates = {"CHEAP": bars_from_closes(closes), "EXPENSIVE": bars_from_closes(closes)}
        metadata = {
            "CHEAP": meta("CHEAP", expense_ratio=0.001),
            "EXPENSIVE": meta("EXPENSIVE", expense_ratio=0.02),
        }
        scores = scorer.score_universe(candidates, metadata)
        by_symbol = {s.symbol: s for s in scores}
        self.assertGreater(by_symbol["CHEAP"].composite_score, by_symbol["EXPENSIVE"].composite_score)

    def test_lower_volatility_scores_higher(self) -> None:
        scorer = ETFScorer()
        stable = [100 + (0.01 if i % 2 == 0 else -0.01) for i in range(100)]
        volatile = [100 + (10 if i % 2 == 0 else -10) for i in range(100)]
        candidates = {"STABLE": bars_from_closes(stable), "VOLATILE": bars_from_closes(volatile)}
        metadata = {"STABLE": meta("STABLE"), "VOLATILE": meta("VOLATILE")}
        scores = scorer.score_universe(candidates, metadata)
        by_symbol = {s.symbol: s for s in scores}
        self.assertGreater(by_symbol["STABLE"].composite_score, by_symbol["VOLATILE"].composite_score)

    def test_rank_assigned_in_score_order(self) -> None:
        scorer = ETFScorer()
        candidates = {
            "A": bars_from_closes([100 + i for i in range(60)]),
            "B": bars_from_closes([100] * 60),
            "C": bars_from_closes([100 - i * 0.1 for i in range(60)]),
        }
        metadata = {s: meta(s, aum_crores=100.0 + i * 100) for i, s in enumerate(candidates)}
        scores = scorer.score_universe(candidates, metadata)
        ranks = sorted(s.rank for s in scores)
        self.assertEqual(ranks, [1, 2, 3])
        # Highest composite score must have rank 1.
        best = max(scores, key=lambda s: s.composite_score)
        self.assertEqual(best.rank, 1)


class TestMissingDataHandling(unittest.TestCase):
    def test_missing_metadata_contributes_zero_not_penalty(self) -> None:
        scorer = ETFScorer()
        closes = [100] * 60
        candidates = {"HAS_AUM": bars_from_closes(closes), "NO_AUM": bars_from_closes(closes)}
        metadata = {
            "HAS_AUM": meta("HAS_AUM", aum_crores=500.0),
            "NO_AUM": meta("NO_AUM", aum_crores=None),
        }
        scores = scorer.score_universe(candidates, metadata)
        no_aum_score = next(s for s in scores if s.symbol == "NO_AUM")
        aum_metric = next(m for m in no_aum_score.metric_scores if m.metric_name == "aum")
        self.assertIsNone(aum_metric.z_score)
        self.assertEqual(aum_metric.contribution, 0.0)
        self.assertIn("unavailable", aum_metric.note)

    def test_single_candidate_universe_all_metrics_neutral(self) -> None:
        # With only 1 candidate, z-scores are undefined (need >=2 for a
        # meaningful distribution) -> every price-derived metric should be
        # None/0-contribution, not an arbitrary number.
        scorer = ETFScorer()
        scores = scorer.score_universe(
            {"ONLY": bars_from_closes([100] * 60)}, {"ONLY": meta("ONLY", aum_crores=100.0)}
        )
        self.assertEqual(scores[0].composite_score, 0.0)


class TestCorrelationAndDiversification(unittest.TestCase):
    def test_diversification_none_without_current_holdings(self) -> None:
        scorer = ETFScorer()
        scores = scorer.score_universe(
            {"X": bars_from_closes([100] * 60)}, {"X": meta("X", asset_class="gold")}
        )
        div_metric = next(m for m in scores[0].metric_scores if m.metric_name == "diversification")
        self.assertIsNone(div_metric.raw_value)

    def test_diversification_higher_for_distinct_asset_class(self) -> None:
        scorer = ETFScorer()
        closes = [100] * 60
        candidates = {"NEW_GOLD": bars_from_closes(closes), "NEW_EQUITY": bars_from_closes(closes)}
        metadata = {
            "NEW_GOLD": meta("NEW_GOLD", asset_class="gold"),
            "NEW_EQUITY": meta("NEW_EQUITY", asset_class="equity_large_cap"),
        }
        holdings_bars = {"HOLD1": bars_from_closes(closes)}
        holdings_meta = {"HOLD1": meta("HOLD1", asset_class="equity_large_cap")}
        scores = scorer.score_universe(candidates, metadata, holdings_bars, holdings_meta)
        by_symbol = {s.symbol: s for s in scores}
        gold_div = next(m for m in by_symbol["NEW_GOLD"].metric_scores if m.metric_name == "diversification")
        equity_div = next(m for m in by_symbol["NEW_EQUITY"].metric_scores if m.metric_name == "diversification")
        self.assertGreater(gold_div.raw_value, equity_div.raw_value)

    def test_correlation_computed_against_portfolio_aggregate(self) -> None:
        scorer = ETFScorer()
        n = 60
        trending = [100 + i for i in range(n)]
        candidates = {"TRENDING_TOO": bars_from_closes(trending)}
        metadata = {"TRENDING_TOO": meta("TRENDING_TOO")}
        holdings_bars = {"HOLD1": bars_from_closes(trending)}  # identical trend -> high correlation
        holdings_meta = {"HOLD1": meta("HOLD1")}
        scores = scorer.score_universe(candidates, metadata, holdings_bars, holdings_meta)
        corr_metric = next(m for m in scores[0].metric_scores if m.metric_name == "correlation")
        self.assertIsNotNone(corr_metric.raw_value)
        self.assertGreater(corr_metric.raw_value, 0.9)  # near-identical trend -> near-1 correlation


if __name__ == "__main__":
    unittest.main()
