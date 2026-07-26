"""Walk-forward in-sample vs out-of-sample reporting (Milestone 5A,
requirement 7). Phase 4's frozen WalkForwardValidator.run() only backtests
each window's TEST (out-of-sample) period -- it never runs the TRAIN
period through the engine at all, so there's nothing to compare
in-sample-vs-out-of-sample against without this wrapper. This module adds
exactly that: for each window the frozen validator already generated, run
a second backtest over the TRAIN period with the identical strategy
configuration, and report both side by side. WalkForwardValidator itself
is never modified.

LOOK-AHEAD BIAS CAVEAT, found during adversarial review (requirement 9) --
stated prominently rather than left implicit: this module does NOT itself
guarantee the absence of look-ahead bias. It structurally separates WHEN
train and test periods are backtested, but if the caller's
strategy_factory closure computes target_weights (e.g. via Portfolio
Optimizer) using statistics derived from the FULL dataset -- including
dates beyond the current window's train_end -- that information leaks
into both the "in-sample" and "out-of-sample" runs identically, and the
comparison this module produces would look clean while still being
compromised. The genuine prevention of look-ahead bias depends entirely
on the caller constructing strategy_factory() using ONLY data available
as of each window's train_end, which this module has no way to verify or
enforce from the outside. Any real use of this wrapper must audit the
strategy_factory it's given for this specific failure mode -- it is not
automatically safe just because this wrapper exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.backtesting.models import BacktestConfig
from etf_platform.cost_tax_engine import CostTaxEngine
from etf_platform.performance_analytics.report import build_performance_report


@dataclass(frozen=True)
class WindowComparison:
    window_index: int
    train_start: object
    train_end: object
    test_start: object
    test_end: object
    in_sample_xirr: object
    out_of_sample_xirr: object
    in_sample_max_drawdown: object
    out_of_sample_max_drawdown: object
    in_sample_sharpe: object
    out_of_sample_sharpe: object


@dataclass
class InSampleOutOfSampleReport:
    comparisons: list
    out_of_sample_result: object

    def stability_summary(self):
        gaps = [
            c.in_sample_xirr - c.out_of_sample_xirr
            for c in self.comparisons
            if c.in_sample_xirr is not None and c.out_of_sample_xirr is not None
        ]
        if not gaps:
            return None
        return {
            "mean_gap": sum(gaps) / len(gaps),
            "windows_with_positive_gap": sum(1 for g in gaps if g > 0),
            "total_windows": len(gaps),
        }


def build_in_sample_out_of_sample_report(
    validator, bars_by_symbol, strategy_factory, base_config, cost_tax_engine_factory=None,
):
    out_of_sample_result = validator.run(bars_by_symbol, strategy_factory, base_config, cost_tax_engine_factory)

    comparisons = []
    for window, oos_report in zip(out_of_sample_result.windows, out_of_sample_result.per_window_reports):
        strategy = strategy_factory()
        cte = cost_tax_engine_factory() if cost_tax_engine_factory else CostTaxEngine()
        train_config = BacktestConfig(
            start_date=window.train_start, end_date=window.train_end,
            initial_capital=base_config.initial_capital, symbols=base_config.symbols,
            benchmark_symbol=base_config.benchmark_symbol,
        )
        engine = BacktestEngine(train_config, strategy, cte)
        train_result = engine.run(bars_by_symbol)

        in_sample_report = None
        if len(train_result.equity_curve) >= 2:
            realized_pnls = [rg.gross_gain for trade in train_result.trades for rg in trade.realized_gains]
            curve = [(p.as_of_date, p.total_value) for p in train_result.equity_curve]
            in_sample_report = build_performance_report(curve, realized_pnls)

        comparisons.append(WindowComparison(
            window_index=window.window_index, train_start=window.train_start, train_end=window.train_end,
            test_start=window.test_start, test_end=window.test_end,
            in_sample_xirr=in_sample_report.xirr_value if in_sample_report else None,
            out_of_sample_xirr=oos_report.xirr_value if oos_report else None,
            in_sample_max_drawdown=in_sample_report.max_drawdown if in_sample_report else None,
            out_of_sample_max_drawdown=oos_report.max_drawdown if oos_report else None,
            in_sample_sharpe=in_sample_report.sharpe if in_sample_report else None,
            out_of_sample_sharpe=oos_report.sharpe if oos_report else None,
        ))

    return InSampleOutOfSampleReport(comparisons=comparisons, out_of_sample_result=out_of_sample_result)
