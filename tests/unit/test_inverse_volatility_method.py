"""Unit tests for InverseVolatilityMethod - hand-verified against known values."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer.methods.inverse_volatility import InverseVolatilityMethod
from etf_platform.risk_management.models import SoftPreferences


def bars_from_returns(returns: list[float], start_price: float = 100.0, start: date = date(2024, 1, 1)) -> list[OHLCVBar]:
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return [
        OHLCVBar("X", start + timedelta(days=i), p, p + 0.5, p - 0.5, p, 10000)
        for i, p in enumerate(prices)
    ]


def make_score(symbol: str) -> ETFScore:
    return ETFScore(symbol=symbol, composite_score=0.0, metric_scores=())


class TestInverseVolatilityWeights(unittest.TestCase):
    def setUp(self) -> None:
        self.method = InverseVolatilityMethod()

    def test_lower_volatility_gets_higher_weight(self) -> None:
        import numpy as np

        rng = np.random.default_rng(1)
        low_vol_returns = list(rng.normal(0.0003, 0.003, 300))
        high_vol_returns = list(rng.normal(0.0003, 0.03, 300))

        candidates = [make_score("LOW_VOL"), make_score("HIGH_VOL")]
        price_history = {
            "LOW_VOL": bars_from_returns(low_vol_returns),
            "HIGH_VOL": bars_from_returns(high_vol_returns),
        }
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())

        self.assertGreater(result["LOW_VOL"][0], result["HIGH_VOL"][0])

    def test_equal_volatility_gets_equal_weight(self) -> None:
        import numpy as np

        rng = np.random.default_rng(2)
        returns = list(rng.normal(0.0003, 0.01, 300))

        candidates = [make_score("A"), make_score("B")]
        price_history = {"A": bars_from_returns(returns), "B": bars_from_returns(returns)}
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())

        self.assertAlmostEqual(result["A"][0], result["B"][0], places=6)
        self.assertAlmostEqual(result["A"][0], 0.5, places=6)

    def test_weights_sum_to_one(self) -> None:
        import numpy as np

        rng = np.random.default_rng(3)
        candidates = [make_score(s) for s in ["A", "B", "C"]]
        price_history = {
            s: bars_from_returns(list(rng.normal(0.0003, 0.005 * (i + 1), 300)))
            for i, s in enumerate(["A", "B", "C"])
        }
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())
        total = sum(w for w, _ in result.values())
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_known_three_asset_example_hand_computed(self) -> None:
        def alternating(amplitude: float, n: int) -> list[float]:
            return [amplitude if i % 2 == 0 else -amplitude for i in range(n)]

        candidates = [make_score(s) for s in ["LOW", "MED", "HIGH"]]
        price_history = {
            "LOW": bars_from_returns(alternating(0.01, 300)),
            "MED": bars_from_returns(alternating(0.02, 300)),
            "HIGH": bars_from_returns(alternating(0.04, 300)),
        }
        method = InverseVolatilityMethod()
        result = method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())

        ratio_low_high = result["LOW"][0] / result["HIGH"][0]
        self.assertAlmostEqual(ratio_low_high, 4.0, delta=0.5)
        ratio_med_high = result["MED"][0] / result["HIGH"][0]
        self.assertAlmostEqual(ratio_med_high, 2.0, delta=0.3)

    def test_insufficient_history_excludes_symbol(self) -> None:
        candidates = [make_score("SHORT"), make_score("LONG")]
        price_history = {
            "SHORT": bars_from_returns([0.001]),
            "LONG": bars_from_returns([0.001] * 300),
        }
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())
        self.assertNotIn("SHORT", result)
        self.assertIn("LONG", result)

    def test_no_candidates_have_valid_data_returns_empty(self) -> None:
        candidates = [make_score("A")]
        price_history = {"A": []}
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())
        self.assertEqual(result, {})

    def test_explainability_components_present(self) -> None:
        import numpy as np

        rng = np.random.default_rng(5)
        candidates = [make_score("A")]
        price_history = {"A": bars_from_returns(list(rng.normal(0.0003, 0.01, 300)))}
        result = self.method.compute_raw_weights(candidates, price_history, {}, SoftPreferences())
        _, components = result["A"]
        factor_names = {c.factor_name for c in components}
        self.assertIn("annualized_volatility", factor_names)
        self.assertIn("normalized_weight", factor_names)


if __name__ == "__main__":
    unittest.main()
