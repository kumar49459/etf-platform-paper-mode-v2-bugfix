"""End-to-end tests for PortfolioCandidateGenerator — exercises the full
Phase 3 pipeline: metadata -> screening -> scoring -> statistical
validation -> recommendation (or explicit non-recommendation)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml

from etf_platform.data_engine.models import InstrumentMeta, OHLCVBar
from etf_platform.etf_optimizer.candidate_generator import PortfolioCandidateGenerator
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager
from etf_platform.etf_optimizer.models import ScreeningThresholds


def bars_from_returns(returns: np.ndarray, start_price: float = 100.0, start: date = date(2024, 1, 1)) -> list[OHLCVBar]:
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [
        OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 200000)
        for i, p in enumerate(prices)
    ]


class FakeDataEngine:
    def __init__(self, bars_by_symbol: dict[str, list[OHLCVBar]]) -> None:
        self._bars = bars_by_symbol

    def get_instrument_master(self) -> list[InstrumentMeta]:
        return [InstrumentMeta(symbol, symbol, "NSE", i) for i, symbol in enumerate(self._bars)]

    def get_ohlcv(self, symbols, start, end, snapshot_id=None) -> dict[str, list[OHLCVBar]]:
        result = {}
        for symbol in symbols:
            bars = self._bars.get(symbol, [])
            result[symbol] = [b for b in bars if start <= b.trade_date <= end]
        return result


class TestPortfolioCandidateGenerator(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.overrides_path = self.tmp_dir / "overrides.yaml"

    def _build(self, overrides: dict, bars: dict[str, list[OHLCVBar]], thresholds=None):
        self.overrides_path.write_text(yaml.safe_dump({"etfs": overrides}), encoding="utf-8")
        data_engine = FakeDataEngine(bars)
        metadata_manager = ETFMetadataManager(data_engine, self.overrides_path)
        generator = PortfolioCandidateGenerator(
            data_engine, metadata_manager, thresholds or ScreeningThresholds(min_trading_days_history=200)
        )
        return generator

    def test_validated_recommendation_emitted_for_clear_outperformer(self) -> None:
        rng = np.random.default_rng(42)
        n = 300
        incumbent_returns = rng.normal(0.0002, 0.008, n)
        # A clearly, consistently better peer in the same asset class.
        better_returns = incumbent_returns + 0.0025

        bars = {
            "INCUMBENT": bars_from_returns(incumbent_returns),
            "BETTER_PEER": bars_from_returns(better_returns),
        }
        overrides = {
            "INCUMBENT": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
            "BETTER_PEER": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)

        report = generator.generate(
            universe_symbols=["INCUMBENT", "BETTER_PEER"],
            current_holdings=["INCUMBENT"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )

        self.assertEqual(len(report.replacement_recommendations), 1)
        rec = report.replacement_recommendations[0]
        self.assertEqual(rec.incumbent_symbol, "INCUMBENT")
        self.assertEqual(rec.candidate_symbol, "BETTER_PEER")
        self.assertTrue(rec.test_result.is_significant)
        self.assertTrue(rec.test_result.favors_candidate)

    def test_no_recommendation_when_difference_is_noise(self) -> None:
        rng = np.random.default_rng(7)
        n = 250
        base = rng.normal(0.0003, 0.01, n)
        similar = base + rng.normal(0, 0.0001, n)  # essentially the same process

        bars = {"INCUMBENT": bars_from_returns(base), "SIMILAR_PEER": bars_from_returns(similar)}
        overrides = {
            "INCUMBENT": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
            "SIMILAR_PEER": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)

        report = generator.generate(
            universe_symbols=["INCUMBENT", "SIMILAR_PEER"],
            current_holdings=["INCUMBENT"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )
        self.assertEqual(len(report.replacement_recommendations), 0)

    def test_no_recommendation_across_different_asset_classes(self) -> None:
        rng = np.random.default_rng(1)
        n = 300
        equity_returns = rng.normal(0.0001, 0.01, n)
        gold_returns = rng.normal(0.002, 0.005, n)  # dramatically "better" but different asset class

        bars = {"EQUITY_HOLD": bars_from_returns(equity_returns), "GOLD_ETF": bars_from_returns(gold_returns)}
        overrides = {
            "EQUITY_HOLD": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
            "GOLD_ETF": {"asset_class": "gold", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)

        report = generator.generate(
            universe_symbols=["EQUITY_HOLD", "GOLD_ETF"],
            current_holdings=["EQUITY_HOLD"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )
        # GOLD_ETF must never be suggested as a replacement for EQUITY_HOLD
        # even though it clearly "scores better" — different asset class.
        self.assertEqual(len(report.replacement_recommendations), 0)

    def test_unknown_asset_class_holding_reported_as_insufficient_data(self) -> None:
        rng = np.random.default_rng(1)
        n = 250
        returns = rng.normal(0.0003, 0.01, n)
        bars = {"MYSTERY": bars_from_returns(returns), "OTHER": bars_from_returns(returns + 0.001)}
        overrides = {
            "MYSTERY": {"asset_class": None, "aum_crores": 1000.0},
            "OTHER": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)

        report = generator.generate(
            universe_symbols=["MYSTERY", "OTHER"],
            current_holdings=["MYSTERY"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )
        self.assertIn("MYSTERY", report.holdings_with_insufficient_data)
        self.assertEqual(len(report.replacement_recommendations), 0)

    def test_drawdown_tradeoff_note_present_when_relevant(self) -> None:
        rng = np.random.default_rng(3)
        n = 300
        incumbent_returns = rng.normal(0.0002, 0.006, n)
        candidate_returns = incumbent_returns.copy() + 0.002
        candidate_returns[150] = -0.30  # engineered crash

        bars = {
            "INCUMBENT": bars_from_returns(incumbent_returns),
            "RISKY_PEER": bars_from_returns(candidate_returns),
        }
        overrides = {
            "INCUMBENT": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
            "RISKY_PEER": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)
        report = generator.generate(
            universe_symbols=["INCUMBENT", "RISKY_PEER"],
            current_holdings=["INCUMBENT"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )
        if report.replacement_recommendations:  # only assert the note if a recommendation was in fact made
            rec = report.replacement_recommendations[0]
            if rec.test_result.drawdown_worse:
                self.assertIn("CAUTION", rec.drawdown_tradeoff_note)
                self.assertIn("manual review", rec.drawdown_tradeoff_note)

    def test_current_holdings_scored_alongside_universe(self) -> None:
        """A current holding must be scored on equal footing with the rest
        of the universe (see generate()'s full_universe construction) even
        if the caller forgot to include it in universe_symbols."""
        rng = np.random.default_rng(5)
        n = 250
        returns = rng.normal(0.0002, 0.01, n)
        bars = {"HOLDING_ONLY": bars_from_returns(returns), "OTHER": bars_from_returns(returns)}
        overrides = {
            "HOLDING_ONLY": {"asset_class": "equity_large_cap", "aum_crores": 1000.0},
            "OTHER": {"asset_class": "gold", "aum_crores": 1000.0},
        }
        generator = self._build(overrides, bars)
        report = generator.generate(
            universe_symbols=["OTHER"],  # deliberately omits HOLDING_ONLY
            current_holdings=["HOLDING_ONLY"],
            lookback_days=400,
            as_of=date(2024, 1, 1) + timedelta(days=n),
        )
        self.assertIsNotNone(report.universe_report.get_score("HOLDING_ONLY"))


if __name__ == "__main__":
    unittest.main()
