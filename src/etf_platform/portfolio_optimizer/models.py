"""Portfolio Optimizer core models (Phase 5).

Output is ALWAYS weights (percentages), never amounts or quantities -- the
binding capital-agnostic rule from §15. Nothing in this package imports or
references an absolute rupee amount anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OptimizationMethod(str, Enum):
    INVERSE_VOLATILITY = "inverse_volatility"
    # Future, per the mandatory pluggable-method requirement -- NOT
    # implemented yet. Listed here only so the enum is forward-compatible
    # without needing every caller to change when one is added; each is
    # unusable until a corresponding AllocationMethod is registered (see
    # methods/base.py's registry) -- selecting one that isn't registered
    # raises a clear error, never silently falls back to a different method.
    RISK_PARITY = "risk_parity"
    MINIMUM_VARIANCE = "minimum_variance"
    BLACK_LITTERMAN = "black_litterman"
    HRP = "hierarchical_risk_parity"


@dataclass(frozen=True)
class WeightComponent:
    """One factor contributing to a single ETF's target weight -- the
    explainability requirement (F3), same spirit as Phase 3's MetricScore."""

    factor_name: str
    raw_value: float | None
    note: str = ""


@dataclass(frozen=True)
class TargetWeight:
    symbol: str
    weight: float
    method_used: OptimizationMethod
    components: tuple[WeightComponent, ...]
    was_capped: bool = False
    cap_reason: str = ""

    @property
    def explanation(self) -> str:
        parts = [f"{self.symbol}: {self.weight:.2%} via {self.method_used.value}."]
        for c in self.components:
            if c.raw_value is not None:
                parts.append(f"{c.factor_name}={c.raw_value:.4f}")
            elif c.note:
                parts.append(f"{c.factor_name}: {c.note}")
        if self.was_capped:
            parts.append(f"Capped: {self.cap_reason}")
        return " ".join(parts)


@dataclass
class OptimizationResult:
    feasible: bool
    target_weights: tuple[TargetWeight, ...] = field(default_factory=tuple)
    excluded_symbols: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    cash_reserve_pct: float = 0.0
    infeasibility_reason: str = ""
    method_used: OptimizationMethod | None = None

    def weights_dict(self) -> dict[str, float]:
        return {tw.symbol: tw.weight for tw in self.target_weights}

    def total_invested_pct(self) -> float:
        return sum(tw.weight for tw in self.target_weights)
