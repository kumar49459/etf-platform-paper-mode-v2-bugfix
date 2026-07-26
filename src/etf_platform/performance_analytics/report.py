"""Performance report builder — combines the individual metric functions in
metrics.py into the single structured report Phase 4 objective #10 requires
(XIRR, CAGR, Sharpe, Sortino, Calmar, max drawdown, rolling returns,
win/loss stats, benchmark comparison).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from etf_platform.performance_analytics.metrics import (
    WinLossStats,
    cagr,
    calmar_ratio,
    max_drawdown_from_equity_curve,
    rolling_returns,
    sharpe_ratio,
    sortino_ratio,
    win_loss_stats,
    xirr,
)


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark_symbol: str
    benchmark_total_return: float | None
    strategy_total_return: float | None
    excess_return: float | None       # strategy - benchmark, simple difference
    correlation: float | None
    tracking_error_annualized: float | None  # stdev of (strategy - benchmark) daily returns, annualized


@dataclass(frozen=True)
class PerformanceReport:
    start_date: date
    end_date: date
    initial_capital: float
    final_value: float
    xirr_value: float | None
    cagr_value: float | None
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float | None
    rolling_returns_1y: tuple[tuple[date, float], ...]
    win_loss: WinLossStats
    benchmark: BenchmarkComparison | None

    def summary_dict(self) -> dict:
        """Flat dict for persistence (backtest_runs.metrics_json, per Phase 1 §6)."""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": self.initial_capital,
            "final_value": self.final_value,
            "xirr": self.xirr_value,
            "cagr": self.cagr_value,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_loss.win_rate,
            "total_trades": self.win_loss.total_trades,
            "profit_factor": self.win_loss.profit_factor,
            "benchmark_excess_return": self.benchmark.excess_return if self.benchmark else None,
        }


def _daily_returns_from_curve(equity_curve: list[tuple[date, float]]) -> np.ndarray:
    values = np.array([v for _, v in equity_curve], dtype=float)
    if len(values) < 2:
        return np.array([])
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = (values[1:] - values[:-1]) / values[:-1]
    return returns[np.isfinite(returns)]


def _build_benchmark_comparison(
    equity_curve: list[tuple[date, float]],
    benchmark_curve: list[tuple[date, float]] | None,
    benchmark_symbol: str,
) -> BenchmarkComparison | None:
    if not benchmark_curve or len(benchmark_curve) < 2:
        return None

    strategy_by_date = dict(equity_curve)
    benchmark_by_date = dict(benchmark_curve)
    common_dates = sorted(set(strategy_by_date) & set(benchmark_by_date))
    if len(common_dates) < 2:
        return None

    strategy_values = [strategy_by_date[d] for d in common_dates]
    benchmark_values = [benchmark_by_date[d] for d in common_dates]

    strategy_total_return = (strategy_values[-1] / strategy_values[0]) - 1.0 if strategy_values[0] > 0 else None
    benchmark_total_return = (benchmark_values[-1] / benchmark_values[0]) - 1.0 if benchmark_values[0] > 0 else None
    excess_return = (
        strategy_total_return - benchmark_total_return
        if strategy_total_return is not None and benchmark_total_return is not None
        else None
    )

    strategy_returns = _daily_returns_from_curve(list(zip(common_dates, strategy_values)))
    benchmark_returns = _daily_returns_from_curve(list(zip(common_dates, benchmark_values)))
    n = min(len(strategy_returns), len(benchmark_returns))
    correlation = None
    tracking_error = None
    if n >= 2:
        sr, br = strategy_returns[:n], benchmark_returns[:n]
        if np.std(sr) > 0 and np.std(br) > 0:
            correlation = float(np.corrcoef(sr, br)[0, 1])
        diff = sr - br
        if len(diff) >= 2:
            tracking_error = float(np.std(diff, ddof=1) * np.sqrt(252))

    return BenchmarkComparison(
        benchmark_symbol=benchmark_symbol,
        benchmark_total_return=benchmark_total_return,
        strategy_total_return=strategy_total_return,
        excess_return=excess_return,
        correlation=correlation,
        tracking_error_annualized=tracking_error,
    )


def build_performance_report(
    equity_curve: list[tuple[date, float]],
    realized_trade_pnls: list[float],
    benchmark_curve: list[tuple[date, float]] | None = None,
    benchmark_symbol: str = "benchmark",
    external_cashflows: list[tuple[date, float]] | None = None,
    risk_free_rate: float = 0.0,
) -> PerformanceReport:
    """Build the full Phase 4 performance report from a backtest's equity
    curve and closed-trade P&L list.

    `external_cashflows`, if provided, overrides the default XIRR
    computation (initial investment out, final value in) — use this if the
    backtest models periodic contributions/withdrawals rather than a single
    lump sum. Default: derived from the equity curve's first/last points.
    """
    if len(equity_curve) < 2:
        raise ValueError("equity_curve must have at least 2 points to build a performance report.")

    sorted_curve = sorted(equity_curve, key=lambda x: x[0])
    start_date, initial_capital = sorted_curve[0]
    end_date, final_value = sorted_curve[-1]
    years = (end_date - start_date).days / 365.0

    cashflows = external_cashflows or [(start_date, -initial_capital), (end_date, final_value)]
    xirr_value = xirr(cashflows)
    cagr_value = cagr(initial_capital, final_value, years)

    daily_returns = _daily_returns_from_curve(sorted_curve)
    sharpe = sharpe_ratio(daily_returns, risk_free_rate) if len(daily_returns) >= 2 else None
    sortino = sortino_ratio(daily_returns, risk_free_rate) if len(daily_returns) >= 2 else None
    max_dd = max_drawdown_from_equity_curve([v for _, v in sorted_curve])
    calmar = calmar_ratio(cagr_value, max_dd)

    rolling_1y = rolling_returns(sorted_curve, window_days=365)
    win_loss = win_loss_stats(realized_trade_pnls)
    benchmark = _build_benchmark_comparison(sorted_curve, benchmark_curve, benchmark_symbol)

    return PerformanceReport(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_value=final_value,
        xirr_value=xirr_value,
        cagr_value=cagr_value,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        max_drawdown=max_dd,
        rolling_returns_1y=tuple(rolling_1y),
        win_loss=win_loss,
        benchmark=benchmark,
    )
