"""Exceptions raised by the Risk Management Engine."""


class RiskManagementError(Exception):
    """Base class for all Risk Management Engine errors."""


class InvalidConstraintsError(RiskManagementError):
    """Raised when a RiskConstraints object fails validation (e.g. a
    max_weight_per_etf greater than max_weight_per_asset_class)."""


class ManualSellingViolationError(RiskManagementError):
    """Raised if any code path attempts to construct a RiskEvent whose
    recommended_action reads as a sell instruction. Per the binding
    decision recorded in PHASE5_Objectives.md, Risk Management Engine may
    detect, alert, and recommend protective non-sell actions only -- it
    can never create or submit a sell proposal. This exception firing
    indicates a bug in whatever code tried to construct the event, not a
    normal condition to handle gracefully.
    """
