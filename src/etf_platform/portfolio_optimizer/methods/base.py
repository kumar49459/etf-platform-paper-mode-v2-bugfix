"""Pluggable AllocationMethod interface and registry (Phase 5, mandatory
per your approval: "the design must remain modular so that future methods
(Risk Parity, Minimum Variance, Black-Litterman, HRP, etc.) can be plugged
in without changing other modules").

Same adapter-pattern discipline already used throughout this platform --
DataProvider (Phase 2), SecretsProvider (Phase 2), TimeSeriesStore (Phase
2) -- applied to allocation methodology. `PortfolioOptimizer` (optimizer.py)
depends only on this interface, never on a concrete method; adding
RiskParityMethod later means writing one new class and registering it,
with zero changes to optimizer.py, risk_management, or anything else.

Deliberate design choice: a method computes RAW weights and explainability
components only -- hard-constraint capping (max weight per ETF/asset class)
is applied uniformly, once, by `optimizer.py` after any method runs. This
means every current and future method automatically gets correct,
consistent constraint enforcement without having to implement capping
logic itself, and a bug in the capping algorithm only needs fixing in one
place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer.models import OptimizationMethod, WeightComponent
from etf_platform.risk_management.models import SoftPreferences


class AllocationMethod(ABC):
    """Base class every pluggable allocation methodology implements."""

    @property
    @abstractmethod
    def method_id(self) -> OptimizationMethod:
        ...

    @abstractmethod
    def compute_raw_weights(
        self,
        candidates: list[ETFScore],
        price_history: dict[str, list[OHLCVBar]],
        current_holdings: dict[str, float],
        soft_preferences: SoftPreferences,
    ) -> dict[str, tuple[float, list[WeightComponent]]]:
        """Return {symbol: (raw_weight, explainability_components)} for
        every candidate this method can price. Raw weights should sum to
        1.0 across the symbols returned (excluding any the method itself
        chooses to drop for insufficient data -- those go in the return
        dict simply absent; the authoritative exclusion+reason handling
        happens in optimizer.py, not here).

        Must NOT apply hard constraints (max weight caps) -- that's
        optimizer.py's job, applied uniformly across all methods.
        """


_METHOD_REGISTRY: dict[OptimizationMethod, AllocationMethod] = {}


def register_method(method: AllocationMethod) -> None:
    _METHOD_REGISTRY[method.method_id] = method


def get_method(method_id: OptimizationMethod) -> AllocationMethod:
    from etf_platform.portfolio_optimizer.exceptions import MethodNotRegisteredError

    if method_id not in _METHOD_REGISTRY:
        raise MethodNotRegisteredError(
            f"OptimizationMethod '{method_id.value}' has no registered AllocationMethod implementation. "
            f"Registered methods: {[m.value for m in _METHOD_REGISTRY]}. This is not a silent fallback -- "
            "select a registered method or implement and register the one you need."
        )
    return _METHOD_REGISTRY[method_id]


def registered_methods() -> list[OptimizationMethod]:
    return list(_METHOD_REGISTRY)
