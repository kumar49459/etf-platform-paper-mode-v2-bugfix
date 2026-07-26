"""Exceptions raised by the Strategy Engine."""


class StrategyEngineError(Exception):
    """Base class for all Strategy Engine errors."""


class SellInstructionAttemptedError(StrategyEngineError):
    """Raised if any code path within Strategy Engine attempts to construct
    a SELL OrderIntent. This must be structurally unreachable -- if this
    exception ever fires, it indicates a bug in Strategy Engine's own code,
    not a legitimate business scenario. Manual selling is permanent
    platform policy (Phase 5's binding decision, reaffirmed for Phase 6
    since this is the first module whose entire job is generating
    executable orders)."""


class InvalidFundingStateError(StrategyEngineError):
    """Raised when the persisted funding state machine is found in an
    inconsistent state (e.g. a state value that doesn't correspond to any
    defined FundingState) -- fails loudly rather than guessing."""


class PortNotConfiguredError(StrategyEngineError):
    """Raised when a live-cycle operation is attempted without a required
    port (CashLedgerPort, NotificationPort) configured. Backtesting mode
    (generate_orders) never needs this -- only run_daily_cycle does."""
