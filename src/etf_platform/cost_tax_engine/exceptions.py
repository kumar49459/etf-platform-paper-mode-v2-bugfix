"""Exceptions raised by the Cost & Tax Engine."""


class CostTaxEngineError(Exception):
    """Base class for all Cost & Tax Engine errors."""


class InsufficientLotsError(CostTaxEngineError):
    """Raised when a sell is matched against FIFO tax lots but insufficient
    quantity is held. Indicates an upstream portfolio-state bug (an
    over-sell should never reach this engine), not a normal condition."""
