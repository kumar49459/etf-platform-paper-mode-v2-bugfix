"""Walk-Forward Validation Framework (Phase 1 Module 21).

Runs the Backtesting Engine repeatedly across rolling (or expanding)
out-of-sample windows, each with a FRESH Strategy instance and a FRESH
CostTaxEngine - no state (e.g. a strategy's internal "have I already
bought" flag, or FIFO tax lots) leaks between windows, since each window is
meant to be an independent out-of-sample test, not a continuation of the
previous one.

Design decision - per-window reports summarized statistically, not one
concatenated equity curve: chaining windows into a single curve (each
window resetting to initial_capital) would produce a curve with jarring
resets that doesn't represent any real deployment, and would obscure the
actual question walk-forward validation exists to answer: does this
strategy perform consistently out-of-sample across many different periods,
or did it get lucky on one long backtest? A distribution of per-window
XIRR/Sharpe/drawdown (mean, median, percent of windows profitable,
worst-case drawdown) answers that question directly; a single spliced curve
does not. This is the same overfitting-avoidance principle already applied
elsewhere in this platform (Phase 1 section 5.3's rejection of point-estimate
mean-variance optimization in favor of methods stable across market regimes).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import numpy as np

from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.backtesting.models import BacktestConfig, BacktestResult
from etf_platform.backtesting.strategy import Strategy
from etf_platform.common.logging_setup import get_logger
from etf_platform.cost_tax_engine import CostTaxEngine
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.performance_analytics.report import PerformanceReport, build_performance_report

logger = get_logger("validation.walk_forward")


@dataclass(frozen=True)
class WalkForwardWindow:
    window_index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True)
class WalkForwardSummary:
    num_windows: int
    mean_xirr: float | None
    median_xirr: float | None
    pct_windows_positive_xirr: float | None
    mean_max_drawdown: float | None
    worst_max_drawdown: float | None
    mean_sharpe: float | None


@dataclass
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    out_of_sample_results: list[BacktestResult]
    per_window_reports: list[PerformanceReport | None]
    summary: WalkForwardSummary


class WalkForwardValidator:
    def __init__(
        self,
        train_days: int,
        test_days: int,
        step_days: int | None = None,
        expanding: bool = False,
    ) -> None:
        """expanding=False (default): rolling fixed-size training window.
        expanding=True: training window always starts at the overall
        start date and grows - more training data each window, at the cost
        of early windows being trained on less data than later ones.
        Neither is universally correct - rolling better simulates "you
        only ever have the last N days of history," expanding better
        simulates "you accumulate more history over time and never discard
        it." Defaulting to rolling since it's the more conservative
        assumption about data availability.
        """
        if train_days <= 0 or test_days <= 0:
            raise ValueError("train_days and test_days must be > 0.")
        self._train_days = train_days
        self._test_days = test_days
        self._step_days = step_days or test_days
        self._expanding = expanding

    def generate_windows(self, overall_start: date, overall_end: date) -> list[WalkForwardWindow]:
        windows: list[WalkForwardWindow] = []
        idx = 0
        current_test_start = overall_start + timedelta(days=self._train_days)

        while True:
            test_end = current_test_start + timedelta(days=self._test_days)
            if test_end > overall_end:
                break
            train_start = overall_start if self._expanding else current_test_start - timedelta(days=self._train_days)
            train_end = current_test_start - timedelta(days=1)
            windows.append(WalkForwardWindow(idx, train_start, train_end, current_test_start, test_end))
            idx += 1
            current_test_start += timedelta(days=self._step_days)

        return windows

    def run(
        self,
        bars_by_symbol: dict[str, list[OHLCVBar]],
        strategy_factory: Callable[[], Strategy],
        base_config: BacktestConfig,
        cost_tax_engine_factory: Callable[[], CostTaxEngine] | None = None,
    ) -> WalkForwardResult:
        windows = self.generate_windows(base_config.start_date, base_config.end_date)
        if not windows:
            raise ValueError(
                "No walk-forward windows fit in the given date range with train_days="
                f"{self._train_days}, test_days={self._test_days}. Widen the date range or "
                "shrink the window sizes."
            )

        results: list[BacktestResult] = []
        reports: list[PerformanceReport | None] = []

        for window in windows:
            strategy = strategy_factory()
            cte = cost_tax_engine_factory() if cost_tax_engine_factory else CostTaxEngine()
            window_config = BacktestConfig(
                start_date=window.test_start, end_date=window.test_end,
                initial_capital=base_config.initial_capital, symbols=base_config.symbols,
                benchmark_symbol=base_config.benchmark_symbol,
                limit_order_expiry_days=base_config.limit_order_expiry_days,
                lookback_days_provided_to_strategy=base_config.lookback_days_provided_to_strategy,
            )
            engine = BacktestEngine(window_config, strategy, cte)
            result = engine.run(bars_by_symbol)
            results.append(result)

            report = None
            if len(result.equity_curve) >= 2:
                realized_pnls = [
                    rg.gross_gain for trade in result.trades for rg in trade.realized_gains
                ]
                curve = [(p.as_of_date, p.total_value) for p in result.equity_curve]
                report = build_performance_report(curve, realized_pnls)
            reports.append(report)

            logger.info(
                "Walk-forward window %d [%s -> %s]: %d trades, XIRR=%s",
                window.window_index, window.test_start, window.test_end,
                len(result.trades), report.xirr_value if report else "N/A",
            )

        summary = self._build_summary(reports)
        return WalkForwardResult(
            windows=windows, out_of_sample_results=results, per_window_reports=reports, summary=summary
        )

    @staticmethod
    def _build_summary(reports: list[PerformanceReport | None]) -> WalkForwardSummary:
        valid = [r for r in reports if r is not None]
        xirrs = [r.xirr_value for r in valid if r.xirr_value is not None]
        drawdowns = [r.max_drawdown for r in valid if r.max_drawdown is not None]
        sharpes = [r.sharpe for r in valid if r.sharpe is not None]

        return WalkForwardSummary(
            num_windows=len(reports),
            mean_xirr=float(np.mean(xirrs)) if xirrs else None,
            median_xirr=float(np.median(xirrs)) if xirrs else None,
            pct_windows_positive_xirr=(sum(1 for x in xirrs if x > 0) / len(xirrs)) if xirrs else None,
            mean_max_drawdown=float(np.mean(drawdowns)) if drawdowns else None,
            worst_max_drawdown=float(np.max(drawdowns)) if drawdowns else None,
            mean_sharpe=float(np.mean(sharpes)) if sharpes else None,
        )
