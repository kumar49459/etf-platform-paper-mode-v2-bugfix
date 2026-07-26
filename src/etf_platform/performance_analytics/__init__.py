"""Performance Analytics (Phase 1 Module 8), implemented in Phase 4 because
the Backtesting Engine's required output (objective #10) needs XIRR, CAGR,
Sharpe, Sortino, Calmar, drawdown, rolling returns, win/loss stats, and
benchmark comparison. Already an approved module in the frozen inventory;
this is implementation, not a new module.
"""

from etf_platform.performance_analytics.metrics import (
    cagr,
    calmar_ratio,
    max_drawdown_from_equity_curve,
    rolling_returns,
    sharpe_ratio,
    sortino_ratio,
    win_loss_stats,
    xirr,
)
from etf_platform.performance_analytics.report import PerformanceReport, build_performance_report

__all__ = [
    "xirr",
    "cagr",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown_from_equity_curve",
    "rolling_returns",
    "win_loss_stats",
    "PerformanceReport",
    "build_performance_report",
]
