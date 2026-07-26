"""Allocation drift detection (Phase 1 §12.4's second rebalancing trigger,
Phase 5 F10).

The tolerance band is a PROVISIONAL value (see engine.py and
PHASE5_Objectives.md item 4) -- not permanently hardcoded, explicitly
flagged as needing future backtesting/walk-forward validation to set
properly, per the binding decision from your approval.
"""

from __future__ import annotations

from etf_platform.risk_management.models import RiskEventType, Severity


def detect_drift(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    tolerance_pct: float,
) -> list[tuple[str, float, RiskEventType, Severity]]:
    """Returns a list of (symbol, drift_amount, event_type, severity) for
    every symbol whose |current - target| exceeds `tolerance_pct`. Symbols
    present in only one of the two dicts are treated as drifting from an
    implicit 0.0 weight in the other (e.g., a target that calls for a new
    ETF not yet held, or a held ETF that's no longer in the target)."""
    all_symbols = set(current_weights) | set(target_weights)
    drifted = []
    for symbol in sorted(all_symbols):
        current = current_weights.get(symbol, 0.0)
        target = target_weights.get(symbol, 0.0)
        drift = abs(current - target)
        if drift > tolerance_pct:
            severity = Severity.CRITICAL if drift > tolerance_pct * 2 else Severity.WARNING
            drifted.append((symbol, drift, RiskEventType.ALLOCATION_DRIFT, severity))
    return drifted
