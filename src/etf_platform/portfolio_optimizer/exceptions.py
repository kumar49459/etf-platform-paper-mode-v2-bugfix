"""Exceptions raised by the Portfolio Optimizer."""


class PortfolioOptimizerError(Exception):
    """Base class for all Portfolio Optimizer errors."""


class MethodNotRegisteredError(PortfolioOptimizerError):
    """Raised when an OptimizationMethod is selected that has no
    registered AllocationMethod implementation. Never silently falls back
    to a different method."""


class EmptyCandidateUniverseError(PortfolioOptimizerError):
    """Raised when the candidate universe is empty -- there is nothing to
    optimize over. Refuses to produce a proposal rather than returning an
    empty/meaningless result."""
