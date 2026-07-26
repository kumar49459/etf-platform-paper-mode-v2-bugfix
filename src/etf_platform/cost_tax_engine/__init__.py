"""Cost & Tax Engine (Phase 1 Module 18).

Implemented in Phase 4 because the Backtesting Engine has a hard dependency
on it (Phase 4 objective #6: "Integrate with the Cost & Tax Engine
interfaces defined in the architecture"). This is an already-approved
module from the frozen 26-module inventory (PHASE1_Architecture_SRS.md
§12.5) being implemented now, not a new module — consistent with your
instruction that Version 1.0's architecture is frozen.
"""

from etf_platform.cost_tax_engine.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig
from etf_platform.cost_tax_engine.exceptions import CostTaxEngineError, InsufficientLotsError
from etf_platform.cost_tax_engine.models import CostBreakdown, GainType, RealizedGain, Side, TaxLot

__all__ = [
    "CostTaxEngine",
    "IndiaEquityCostConfig",
    "CostBreakdown",
    "GainType",
    "RealizedGain",
    "Side",
    "TaxLot",
    "CostTaxEngineError",
    "InsufficientLotsError",
]
