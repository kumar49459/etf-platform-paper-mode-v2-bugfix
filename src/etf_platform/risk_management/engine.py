"""Risk Management Engine (Phase 5).

Two jobs, per PHASE5_Objectives.md F8-F12:
  1. Supply the constraints Portfolio Optimizer must respect (get_constraints).
  2. Monitor current portfolio state against those constraints and against
     allocation drift, emitting RiskEvents (evaluate) -- never a sell
     proposal, per the binding manual-selling decision.

MAX DRAWDOWN IS A GATE, NOT A SOLVED CONSTRAINT: unlike max_weight_per_etf
(which Portfolio Optimizer can enforce directly in its weight formula via
capping, see portfolio_optimizer/optimizer.py), max drawdown is a property
of a portfolio's estimated future behavior -- there is no closed-form way
for inverse-volatility (or most allocation formulas) to directly target a
specific drawdown number. It can only be estimated after the fact, by
backtesting the candidate weights. So `check_drawdown_constraint` is a
separate method, called by the proposal-building step (Phase 5's
proposal_builder.py) AFTER a candidate allocation and its backtested
estimate already exist -- not part of `evaluate()`, which only checks
weight-based constraints that are directly and immediately knowable from
current holdings.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from etf_platform.common.logging_setup import get_logger
from etf_platform.risk_management.drift_detection import detect_drift
from etf_platform.risk_management.models import RiskConstraints, RiskEvent, RiskEventType, Severity
from etf_platform.risk_management.registry import RiskEventRegistry

logger = get_logger("risk_management.engine")

def _generate_event_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"risk-{timestamp}-{secrets.token_hex(3)}"


class RiskManagementEngine:
    def __init__(
        self,
        constraints: RiskConstraints | None = None,
        registry: RiskEventRegistry | None = None,
    ) -> None:
        self._constraints = constraints or RiskConstraints()
        self._constraints.validate()
        self._registry = registry

    def get_constraints(self) -> RiskConstraints:
        return self._constraints

    def evaluate(
        self,
        current_weights: dict[str, float],
        asset_class_by_symbol: dict[str, str | None],
        last_approved_weights: dict[str, float] | None = None,
    ) -> list[RiskEvent]:
        events: list[RiskEvent] = []
        hard = self._constraints.hard

        for symbol, weight in current_weights.items():
            if weight > hard.max_weight_per_etf + 1e-9:
                events.append(self._make_event(
                    RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
                    f"{symbol} is {weight:.1%} of the portfolio, exceeding the {hard.max_weight_per_etf:.1%} "
                    "per-ETF limit.",
                    "Review this position. No sell will be proposed automatically -- reducing it, if "
                    "you choose to, is your decision.",
                    symbol=symbol,
                ))

        asset_class_totals: dict[str, float] = {}
        for symbol, weight in current_weights.items():
            asset_class = asset_class_by_symbol.get(symbol)
            if asset_class is not None:
                asset_class_totals[asset_class] = asset_class_totals.get(asset_class, 0.0) + weight
        for asset_class, total in asset_class_totals.items():
            if total > hard.max_weight_per_asset_class + 1e-9:
                events.append(self._make_event(
                    RiskEventType.BREACH_MAX_WEIGHT_PER_ASSET_CLASS, Severity.WARNING,
                    f"Asset class '{asset_class}' is {total:.1%} of the portfolio, exceeding the "
                    f"{hard.max_weight_per_asset_class:.1%} per-asset-class limit.",
                    "Review concentration in this asset class. No sell will be proposed automatically.",
                ))

        if last_approved_weights is not None:
            drift_tolerance_pct = hard.drift_tolerance_pct
            for symbol, drift_amount, event_type, severity in detect_drift(
                current_weights, last_approved_weights, drift_tolerance_pct
            ):
                events.append(self._make_event(
                    event_type, severity,
                    f"{symbol} has drifted {drift_amount:.1%} from its last-approved target weight "
                    f"(tolerance: {drift_tolerance_pct:.1%}, provisional -- see PHASE5_Objectives.md item 4).",
                    "Consider requesting a fresh allocation proposal from the Portfolio Optimizer to "
                    "realign with target weights via future purchases. No sell will be proposed.",
                    symbol=symbol,
                ))

        for event in events:
            if self._registry is not None:
                self._registry.record(event)
        return events

    def check_drawdown_constraint(self, estimated_max_drawdown: float) -> RiskEvent | None:
        if estimated_max_drawdown <= self._constraints.hard.max_drawdown_target + 1e-9:
            return None
        event = self._make_event(
            RiskEventType.BREACH_MAX_DRAWDOWN, Severity.CRITICAL,
            f"Candidate allocation's estimated max drawdown ({estimated_max_drawdown:.1%}) exceeds the "
            f"hard target of {self._constraints.hard.max_drawdown_target:.1%}.",
            "This proposal cannot be marked approval-ready as-is. A lower-drawdown alternative should "
            "be presented alongside it, per the 'present both options' rule (§12.2).",
        )
        if self._registry is not None:
            self._registry.record(event)
        return event

    def request_halt(self, reason: str) -> RiskEvent:
        event = self._make_event(
            RiskEventType.KILL_SWITCH_REQUESTED, Severity.CRITICAL,
            f"Halt requested: {reason}",
            "Escalate to the Approval Console / Kill-Switch chain of authority. No automatic action "
            "is taken by Risk Management Engine itself.",
        )
        if self._registry is not None:
            self._registry.record(event)
        logger.critical("Kill-switch halt requested: %s", reason)
        return event

    @staticmethod
    def _make_event(
        event_type: RiskEventType, severity: Severity, description: str, recommended_action: str,
        symbol: str | None = None,
    ) -> RiskEvent:
        return RiskEvent(
            event_id=_generate_event_id(), timestamp=datetime.now(timezone.utc), event_type=event_type,
            severity=severity, description=description, recommended_action=recommended_action, symbol=symbol,
        )
