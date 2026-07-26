"""Inverse-volatility allocation method (Phase 5's approved default, per
§6.1: chosen over risk parity and minimum-variance because it only needs
each ETF's own variance, not the full covariance matrix -- the least
estimation-error-sensitive of the risk-based family, appropriate for a
small universe with limited independent market history).

Reuses `etf_optimizer.price_metrics.annualized_volatility` (Phase 3)
rather than reimplementing volatility calculation -- one formula, used
everywhere it's needed.
"""

from __future__ import annotations

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import price_metrics
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer.methods.base import AllocationMethod
from etf_platform.portfolio_optimizer.models import OptimizationMethod, WeightComponent
from etf_platform.risk_management.models import SoftPreferences


class InverseVolatilityMethod(AllocationMethod):
    @property
    def method_id(self) -> OptimizationMethod:
        return OptimizationMethod.INVERSE_VOLATILITY

    def compute_raw_weights(
        self,
        candidates: list[ETFScore],
        price_history: dict[str, list[OHLCVBar]],
        current_holdings: dict[str, float],
        soft_preferences: SoftPreferences,
    ) -> dict[str, tuple[float, list[WeightComponent]]]:
        inv_vol_by_symbol: dict[str, float] = {}
        components_by_symbol: dict[str, list[WeightComponent]] = {}

        for score in candidates:
            symbol = score.symbol
            bars = price_history.get(symbol, [])
            vol = price_metrics.annualized_volatility(bars)
            if vol is None or vol <= 0:
                continue
            inv_vol_by_symbol[symbol] = 1.0 / vol
            components_by_symbol[symbol] = [
                WeightComponent("annualized_volatility", vol),
                WeightComponent("inverse_volatility", 1.0 / vol),
            ]

        total = sum(inv_vol_by_symbol.values())
        if total <= 0:
            return {}

        result: dict[str, tuple[float, list[WeightComponent]]] = {}
        for symbol, inv_vol in inv_vol_by_symbol.items():
            raw_weight = inv_vol / total
            components = components_by_symbol[symbol] + [
                WeightComponent("normalized_weight", raw_weight, note="inverse_volatility / sum(inverse_volatility)")
            ]
            result[symbol] = (raw_weight, components)
        return result
