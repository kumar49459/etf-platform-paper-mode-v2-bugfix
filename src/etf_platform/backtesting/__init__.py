"""Backtesting Engine (Phase 1 Module 6, built in Phase 4).

Event-driven, not vectorized (Phase 1 §5.2). Public entry points:
BacktestEngine, Strategy (the interface you implement), BacktestConfig,
CostTaxEngine (Module 18, also implemented this phase), and the
reproducibility/registry helpers for persisting `backtest_runs`.
"""

from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.backtesting.exceptions import (
    BacktestError,
    InvalidOrderError,
    LookAheadViolationError,
    ReproducibilityError,
)
from etf_platform.backtesting.models import (
    BacktestConfig,
    BacktestResult,
    EquityCurvePoint,
    OrderIntent,
    OrderType,
    PortfolioSnapshot,
    ReproducibilityRecord,
    Trade,
)
from etf_platform.backtesting.registry import BacktestRunRegistry, run_and_register
from etf_platform.backtesting.reproducibility import build_reproducibility_record
from etf_platform.backtesting.strategy import Strategy

__all__ = [
    "BacktestEngine",
    "Strategy",
    "BacktestConfig",
    "BacktestResult",
    "OrderIntent",
    "OrderType",
    "Trade",
    "EquityCurvePoint",
    "PortfolioSnapshot",
    "ReproducibilityRecord",
    "BacktestRunRegistry",
    "run_and_register",
    "build_reproducibility_record",
    "BacktestError",
    "InvalidOrderError",
    "LookAheadViolationError",
    "ReproducibilityError",
]
