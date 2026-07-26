"""Proposal Builder (Phase 5, F7).

Packages an OptimizationResult into the exact proposal artifact format the
Approval Console requires (Phase 1 SRS section 13.6): current portfolio,
recommended portfolio, reason, expected XIRR improvement, expected
drawdown impact, risk analysis, confidence score, cost and tax impact,
supporting backtest summary.

TWO DESIGN POINTS THAT MATTER MORE THAN THE REST OF THIS MODULE:

1. BUY-ONLY DIFF (the manual-selling rule, made concrete here): a target
   weight LOWER than the current weight is never translated into a sell
   instruction. It becomes an informational note only
   ("X is now overweight relative to target; no sell will be proposed").
   Only weight INCREASES become actionable buy-side changes. This is the
   literal enforcement point of the binding decision recorded in
   PHASE5_Objectives.md -- get this function wrong and the whole manual-
   selling guarantee is broken regardless of what RiskManagementEngine does
   elsewhere.

2. CAPITAL-AGNOSTIC COST ESTIMATE: cost/tax impact is expressed as a
   PERCENTAGE of transaction value, computed via CostTaxEngine using a
   notional reference amount -- never an absolute rupee figure, since
   Phase 5 never knows the actual Available Investment Pool amount (that's
   a Phase 6/Module 28 concern, section 15). This is exact for the default
   cost config (Zerodha-style zero flat brokerage on delivery, see Phase 4's
   cost_tax_engine.py) and becomes a slight UNDER-estimate at very small
   transaction sizes if a non-zero flat brokerage fee is configured --
   disclosed explicitly in the output, not silently presented as precise.

The comparative backtest (current weights vs. target weights) runs at a
large NOTIONAL capital purely to avoid lot-size rounding distortion in the
simulation -- this number is never exposed in the proposal artifact, only
used internally to compute scale-invariant ratios (XIRR, max drawdown),
which do not depend on the notional amount chosen.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from etf_platform.backtesting import BacktestConfig, BacktestEngine, OrderIntent, OrderType, Strategy
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.performance_analytics import build_performance_report
from etf_platform.performance_analytics.report import PerformanceReport
from etf_platform.portfolio_optimizer.models import OptimizationResult
from etf_platform.risk_management import RiskEvent, RiskManagementEngine

_NOTIONAL_BACKTEST_CAPITAL = 10_000_000.0
_BOOTSTRAP_MIN_OBSERVATIONS = 60


class _StaticWeightStrategy(Strategy):
    """Buys toward fixed target weights once at the start, then holds --
    used internally only, for the comparative backtest."""

    def __init__(self, target_weights: dict[str, float]) -> None:
        self._target_weights = target_weights
        self._done = False

    def generate_orders(self, as_of_date, history, portfolio):
        if self._done:
            return []
        self._done = True
        orders = []
        for symbol, weight in self._target_weights.items():
            bars = history.get(symbol, [])
            if not bars:
                continue
            price = bars[-1].close
            if price <= 0:
                continue
            target_cash = portfolio.total_value * weight
            qty = int(target_cash / price)
            if qty > 0:
                orders.append(
                    OrderIntent(symbol, Side.BUY, OrderType.MARKET, qty, "Comparative backtest static allocation.")
                )
        return orders


@dataclass(frozen=True)
class ProposalArtifact:
    proposal_id: str
    generated_at: datetime
    current_weights: dict
    recommended_weights: dict
    buy_only_changes: dict
    overweight_notes: tuple
    reason: str
    expected_xirr_improvement: object
    expected_drawdown_impact: object
    risk_analysis: tuple
    confidence_score: object
    confidence_note: str
    cost_impact_pct: float
    cost_impact_caveat: str
    supporting_backtest_summary: dict
    alternative_lower_drawdown: object = None


def _buy_only_diff(current, target):
    all_symbols = set(current) | set(target)
    buy_changes = {}
    overweight_notes = []
    for symbol in sorted(all_symbols):
        cur = current.get(symbol, 0.0)
        tgt = target.get(symbol, 0.0)
        if tgt > cur + 1e-9:
            buy_changes[symbol] = tgt - cur
        elif tgt < cur - 1e-9:
            overweight_notes.append(
                f"{symbol}: target weight ({tgt:.1%}) is below current ({cur:.1%}). "
                "No sell will be proposed -- this is informational only. Reducing this position, "
                "if you choose to, is your decision."
            )
    return buy_changes, tuple(overweight_notes)


def _estimate_cost_pct(cost_tax_engine):
    cost = cost_tax_engine.compute_transaction_cost(Side.BUY, price=1.0, quantity=1.0)
    caveat = (
        "Expressed as a percentage of transaction value, independent of the actual amount invested "
        "(Phase 5 never computes with an absolute rupee figure, per the capital-agnostic design "
        "requirement). This is exact if brokerage_flat_per_order is 0 (the default). If a non-zero "
        "flat brokerage fee is configured, this percentage slightly UNDER-estimates true cost at very "
        "small transaction sizes, since a flat fee is a larger fraction of a small trade than a large one."
    )
    return cost.total_cost, caveat


def _run_comparative_backtest(current_weights, target_weights, price_history, lookback_days, as_of):
    involved_symbols = sorted(set(current_weights) | set(target_weights))
    if not involved_symbols:
        return None, None

    all_dates = sorted({b.trade_date for s in involved_symbols for b in price_history.get(s, [])})
    if len(all_dates) < _BOOTSTRAP_MIN_OBSERVATIONS:
        return None, None

    start_date = max(all_dates[0], as_of - timedelta(days=lookback_days))
    end_date = min(all_dates[-1], as_of)
    if start_date >= end_date:
        return None, None

    reports = []
    for weights in (current_weights, target_weights):
        if not weights:
            reports.append(None)
            continue
        config = BacktestConfig(
            start_date=start_date, end_date=end_date, initial_capital=_NOTIONAL_BACKTEST_CAPITAL,
            symbols=tuple(involved_symbols),
        )
        engine = BacktestEngine(config, _StaticWeightStrategy(weights), CostTaxEngine())
        result = engine.run(price_history)
        if len(result.equity_curve) < 2:
            reports.append(None)
            continue
        curve = [(p.as_of_date, p.total_value) for p in result.equity_curve]
        reports.append(build_performance_report(curve, []))

    return reports[0], reports[1]


def _confidence_from_reports(current_report, candidate_report):
    if current_report is None or candidate_report is None:
        return None, "Insufficient overlapping price history to compute a statistical confidence signal."
    if candidate_report.xirr_value is None or current_report.xirr_value is None:
        return None, "XIRR could not be computed for one or both allocations; confidence signal unavailable."

    diff = candidate_report.xirr_value - current_report.xirr_value
    if abs(diff) < 0.001:
        return 0.50, (
            "Candidate and current allocations show a negligible historical difference; treat as "
            "directional only, not a strong signal."
        )
    if diff > 0:
        return 0.65, (
            "Candidate shows a historically better outcome over the comparison window. This is a "
            "single-window backtest comparison, not a walk-forward or bootstrap-validated result -- "
            "treat as directional evidence, not statistical proof."
        )
    return 0.35, (
        "Candidate shows a historically worse outcome over the comparison window than the current "
        "allocation on this specific metric; review carefully before proceeding."
    )


def build_proposal(
    optimization_result: OptimizationResult,
    current_weights: dict,
    asset_class_by_symbol: dict,
    price_history: dict,
    risk_engine: RiskManagementEngine,
    cost_tax_engine: CostTaxEngine = None,
    backtest_lookback_days: int = 365,
    as_of: date = None,
) -> ProposalArtifact:
    if not optimization_result.feasible:
        raise ValueError(
            f"Cannot build a proposal from an infeasible OptimizationResult: {optimization_result.infeasibility_reason}"
        )

    as_of = as_of or date.today()
    cost_tax_engine = cost_tax_engine or CostTaxEngine()
    target_weights = optimization_result.weights_dict()

    buy_changes, overweight_notes = _buy_only_diff(current_weights, target_weights)
    cost_pct, cost_caveat = _estimate_cost_pct(cost_tax_engine)

    current_report, candidate_report = _run_comparative_backtest(
        current_weights, target_weights, price_history, backtest_lookback_days, as_of
    )
    xirr_improvement = (
        candidate_report.xirr_value - current_report.xirr_value
        if candidate_report and current_report
        and candidate_report.xirr_value is not None and current_report.xirr_value is not None
        else None
    )
    drawdown_impact = (
        candidate_report.max_drawdown - current_report.max_drawdown
        if candidate_report and current_report
        and candidate_report.max_drawdown is not None and current_report.max_drawdown is not None
        else None
    )
    confidence_score, confidence_note = _confidence_from_reports(current_report, candidate_report)

    risk_events = list(
        risk_engine.evaluate(target_weights, asset_class_by_symbol, last_approved_weights=current_weights)
    )
    if candidate_report is not None and candidate_report.max_drawdown is not None:
        drawdown_event = risk_engine.check_drawdown_constraint(candidate_report.max_drawdown)
        if drawdown_event is not None:
            risk_events.append(drawdown_event)

    method_name = optimization_result.method_used.value if optimization_result.method_used else "unknown"
    reason = (
        f"Portfolio Optimizer ({method_name}) recommends {len(buy_changes)} allocation increase(s) "
        f"toward target weights. {len(overweight_notes)} position(s) are currently overweight relative "
        "to target -- informational only, no sell proposed."
    )

    if (
        drawdown_impact is not None and drawdown_impact > 0.02
        and xirr_improvement is not None and xirr_improvement > 0
    ):
        reason += (
            " NOTE: estimated drawdown is materially worse than the current allocation's while XIRR "
            "improves. Per the platform's capital-preservation priority (Phase 1 section 12.2), a "
            "lower-drawdown alternative should be solved and reviewed alongside this one before proceeding."
        )

    return ProposalArtifact(
        proposal_id=f"proposal-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}",
        generated_at=datetime.now(timezone.utc),
        current_weights=dict(current_weights),
        recommended_weights=target_weights,
        buy_only_changes=buy_changes,
        overweight_notes=overweight_notes,
        reason=reason,
        expected_xirr_improvement=xirr_improvement,
        expected_drawdown_impact=drawdown_impact,
        risk_analysis=tuple(risk_events),
        confidence_score=confidence_score,
        confidence_note=confidence_note,
        cost_impact_pct=cost_pct,
        cost_impact_caveat=cost_caveat,
        supporting_backtest_summary={
            "current_allocation": current_report.summary_dict() if current_report else None,
            "candidate_allocation": candidate_report.summary_dict() if candidate_report else None,
        },
    )
