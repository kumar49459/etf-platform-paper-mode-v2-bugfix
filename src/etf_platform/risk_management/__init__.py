"""Risk Management Engine (Phase 5, Phase 1 Module 7).

Public entry point: RiskManagementEngine. Supplies constraints to
Portfolio Optimizer and monitors current holdings for breaches/drift.
Never proposes a sell -- see engine.py and PHASE5_Objectives.md's binding
manual-selling decision.
"""

from etf_platform.risk_management.engine import RiskManagementEngine
from etf_platform.risk_management.exceptions import (
    InvalidConstraintsError,
    ManualSellingViolationError,
    RiskManagementError,
)
from etf_platform.risk_management.models import (
    HardConstraints,
    RiskConstraints,
    RiskEvent,
    RiskEventType,
    Severity,
    SoftPreferences,
)
from etf_platform.risk_management.registry import RiskEventRegistry

__all__ = [
    "RiskManagementEngine",
    "RiskConstraints",
    "HardConstraints",
    "SoftPreferences",
    "RiskEvent",
    "RiskEventType",
    "Severity",
    "RiskEventRegistry",
    "RiskManagementError",
    "InvalidConstraintsError",
    "ManualSellingViolationError",
]
