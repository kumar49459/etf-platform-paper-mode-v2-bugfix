"""Exceptions raised by the ETF Universe Optimizer package."""


class ETFOptimizerError(Exception):
    """Base class for all ETF Universe Optimizer errors."""


class MetadataError(ETFOptimizerError):
    """Raised on malformed or unreadable metadata overrides."""


class InsufficientDataError(ETFOptimizerError):
    """Raised when there isn't enough price history to compute a metric or
    run a statistical test reliably. Distinct from 'the answer is no' —
    callers should treat this as 'we cannot know', not as a negative result,
    per Phase 1's fail-safe-default principle."""
