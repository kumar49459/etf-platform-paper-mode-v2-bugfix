"""Exceptions raised by the Backtesting Engine."""


class BacktestError(Exception):
    """Base class for all Backtesting Engine errors."""


class InvalidOrderError(BacktestError):
    """Raised when a Strategy emits a structurally invalid order intent —
    e.g. missing a required rationale (Phase 4 objective #11), a
    non-positive quantity, or a sell exceeding held quantity."""


class LookAheadViolationError(BacktestError):
    """Raised if the engine's own internal invariant — a Strategy is never
    given bars beyond the current simulation date — is somehow violated.
    This should be structurally impossible given the engine's design; if
    this exception ever fires, it means the engine itself has a bug, not
    that a Strategy did something wrong. Kept as a hard runtime assertion
    rather than only a design intention, since "eliminate look-ahead bias"
    is a correctness requirement, not a best-effort goal.
    """


class ReproducibilityError(BacktestError):
    """Raised when a backtest cannot be run with full reproducibility
    metadata (missing code version, config version, or data snapshot id) —
    per Phase 1 §1.4, a backtest without this metadata should not silently
    proceed as if it were reproducible."""
