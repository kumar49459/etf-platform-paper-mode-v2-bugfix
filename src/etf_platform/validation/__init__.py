"""Validation package (Phase 1 Module 21: Walk-Forward Validation
Framework, and Module 19: Monte Carlo Simulation Engine).

Phase 4 objectives #7/#8 ask the Backtesting Engine to "support" walk-
forward validation and Monte Carlo hooks — implemented here as working
modules that consume BacktestEngine, not a redesign of it.
"""

from etf_platform.validation.monte_carlo import MonteCarloResult, MonteCarloSimulator
from etf_platform.validation.walk_forward import WalkForwardResult, WalkForwardValidator, WalkForwardWindow

__all__ = [
    "WalkForwardValidator",
    "WalkForwardWindow",
    "WalkForwardResult",
    "MonteCarloSimulator",
    "MonteCarloResult",
]
