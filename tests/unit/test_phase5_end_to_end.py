"""End-to-end test: Phase 3's real screened/scored universe (using the
actual shipped config/etf_metadata_overrides.yaml) through Phase 5's
Portfolio Optimizer and Risk Management Engine to a full proposal artifact
- proving the whole pipeline works together against the real six named
ETFs, not just against synthetic test fixtures.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from etf_platform.data_engine.models import InstrumentMeta, OHLCVBar
from etf_platform.etf_optimizer import ETFMetadataManager, ETFUniverseOptimizer
from etf_platform.etf_optimizer.models import ScreeningThresholds
from etf_platform.portfolio_optimizer import PortfolioOptimizer, build_proposal
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine


class FakeDataEngine:
    def __init__(self, bars_by_symbol):
        self._bars = bars_by_symbol

    def get_instrument_master(self):
        return [InstrumentMeta(s, s, "NSE", i) for i, s in enumerate(self._bars)]

    def get_ohlcv(self, symbols, start, end, snapshot_id=None):
        return {s: [b for b in self._bars.get(s, []) if start <= b.trade_date <= end] for s in symbols}


class TestPhase3ToPhase5EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

        rng = np.random.default_rng(2026)
        n = 400
        start = date(2024, 6, 1)

        def make_bars(returns, start_price):
            prices = [start_price]
            for r in returns:
                prices.append(prices[-1] * (1 + r))
            return [
                OHLCVBar(sym, start + timedelta(days=i), p * 0.998, p * 1.003, p * 0.995, p, 500000)
                for i, (sym, p) in enumerate([("X", p) for p in prices])
            ]

        indian_equity_factor = rng.normal(0.0004, 0.011, n)
        gold_factor = rng.normal(0.0003, 0.007, n)
        us_factor = rng.normal(0.0005, 0.013, n)

        self.bars = {
            "NIFTYBEES": make_bars(indian_equity_factor + rng.normal(0, 0.002, n), 250),
            "JUNIORBEES": make_bars(indian_equity_factor * 1.1 + rng.normal(0, 0.003, n), 780),
            "HDFCSML250": make_bars(indian_equity_factor * 1.3 + rng.normal(0, 0.006, n), 178),
            "MON100": make_bars(us_factor + rng.normal(0, 0.003, n), 330),
            "GOLDBEES": make_bars(gold_factor + rng.normal(0, 0.001, n), 68),
        }

        self.data_engine = FakeDataEngine(self.bars)
        real_overrides_path = Path(__file__).resolve().parents[2] / "config" / "etf_metadata_overrides.yaml"
        self.metadata_manager = ETFMetadataManager(self.data_engine, real_overrides_path)
        self.as_of = start + timedelta(days=n - 2)

    def test_full_pipeline_produces_a_valid_proposal(self):
        thresholds = ScreeningThresholds(min_trading_days_history=200)
        universe_optimizer = ETFUniverseOptimizer(self.data_engine, self.metadata_manager, thresholds)
        universe_report = universe_optimizer.optimize(
            list(self.bars.keys()), lookback_days=450, as_of=self.as_of
        )
        self.assertGreater(len(universe_report.ranked_scores), 0)

        asset_classes = {
            score.symbol: self.metadata_manager.get_metadata(score.symbol).asset_class
            for score in universe_report.ranked_scores
        }

        risk_engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(
                max_weight_per_etf=0.40, max_weight_per_asset_class=0.60, min_history_days_required=200
            ))
        )
        portfolio_optimizer = PortfolioOptimizer(risk_engine)
        price_history = {s: self.bars[s] for s in [score.symbol for score in universe_report.ranked_scores]}

        optimization_result = portfolio_optimizer.optimize(
            list(universe_report.ranked_scores), asset_classes, price_history
        )
        self.assertTrue(optimization_result.feasible)

        proposal = build_proposal(
            optimization_result, current_weights={}, asset_class_by_symbol=asset_classes,
            price_history=price_history, risk_engine=risk_engine, as_of=self.as_of,
        )

        self.assertEqual(set(proposal.buy_only_changes), set(optimization_result.weights_dict()))
        self.assertEqual(proposal.overweight_notes, ())

        for tw in optimization_result.target_weights:
            self.assertLessEqual(tw.weight, 0.40 + 1e-6)

        self.assertTrue(proposal.reason)
        self.assertIsInstance(proposal.cost_impact_pct, float)
        self.assertIn("current_allocation", proposal.supporting_backtest_summary)


if __name__ == "__main__":
    unittest.main()
