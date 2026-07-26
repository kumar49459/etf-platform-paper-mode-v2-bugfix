"""Integration tests for ETFUniverseOptimizer — wires ETFMetadataManager,
UniverseScreeningEngine, and ETFScorer together against a fake data engine."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import yaml

from etf_platform.data_engine.models import InstrumentMeta, OHLCVBar
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager
from etf_platform.etf_optimizer.models import ScreeningStatus, ScreeningThresholds
from etf_platform.etf_optimizer.universe_optimizer import ETFUniverseOptimizer


def bars_from_closes(closes: list[float], volume: int = 100000, start: date = date(2025, 1, 1)) -> list[OHLCVBar]:
    return [
        OHLCVBar("X", start + timedelta(days=i), c, c + 1, c - 1, c, volume)
        for i, c in enumerate(closes)
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


class TestETFUniverseOptimizer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        overrides_path = self.tmp_dir / "overrides.yaml"
        overrides_path.write_text(
            yaml.safe_dump(
                {
                    "etfs": {
                        "GOOD_A": {"asset_class": "equity_large_cap", "aum_crores": 5000.0},
                        "GOOD_B": {"asset_class": "equity_large_cap", "aum_crores": 3000.0},
                        "TINY": {"asset_class": "equity_small_cap", "aum_crores": 5.0},
                    }
                }
            ),
            encoding="utf-8",
        )

        closes_good = [100 + i * 0.1 for i in range(200)]
        closes_thin_history = [100] * 10  # too little history
        bars = {
            "GOOD_A": bars_from_closes(closes_good, volume=500000),
            "GOOD_B": bars_from_closes([c * 0.9 for c in closes_good], volume=400000),
            "TINY": bars_from_closes(closes_thin_history, volume=100),
        }
        self.data_engine = FakeDataEngine(bars)
        self.metadata_manager = ETFMetadataManager(self.data_engine, overrides_path)

    def test_screening_and_scoring_end_to_end(self) -> None:
        thresholds = ScreeningThresholds(min_trading_days_history=60, min_aum_crores=100.0)
        optimizer = ETFUniverseOptimizer(self.data_engine, self.metadata_manager, thresholds)

        report = optimizer.optimize(
            ["GOOD_A", "GOOD_B", "TINY"], lookback_days=365, as_of=date(2025, 7, 20)
        )

        # TINY fails both history and AUM screening -> excluded.
        self.assertIn("TINY", report.excluded_symbols)
        tiny_result = next(r for r in report.screening_results if r.symbol == "TINY")
        self.assertEqual(tiny_result.overall_status, ScreeningStatus.FAIL)

        # GOOD_A and GOOD_B pass and get scored/ranked.
        scored_symbols = {s.symbol for s in report.ranked_scores}
        self.assertEqual(scored_symbols, {"GOOD_A", "GOOD_B"})
        ranks = sorted(s.rank for s in report.ranked_scores)
        self.assertEqual(ranks, [1, 2])

    def test_get_score_helper(self) -> None:
        thresholds = ScreeningThresholds(min_trading_days_history=60)
        optimizer = ETFUniverseOptimizer(self.data_engine, self.metadata_manager, thresholds)
        report = optimizer.optimize(["GOOD_A", "GOOD_B"], as_of=date(2025, 7, 20))
        self.assertIsNotNone(report.get_score("GOOD_A"))
        self.assertIsNone(report.get_score("NOT_IN_UNIVERSE"))

    def test_empty_universe_produces_empty_report(self) -> None:
        thresholds = ScreeningThresholds(min_trading_days_history=60)
        optimizer = ETFUniverseOptimizer(self.data_engine, self.metadata_manager, thresholds)
        report = optimizer.optimize([], as_of=date(2025, 7, 20))
        self.assertEqual(report.ranked_scores, ())
        self.assertEqual(report.excluded_symbols, ())


if __name__ == "__main__":
    unittest.main()
