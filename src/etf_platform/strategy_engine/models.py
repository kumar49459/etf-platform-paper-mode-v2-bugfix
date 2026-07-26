"""Core data models for the Strategy Engine (Phase 6).

AvailableInvestmentPool is implemented here for the first time - it was
specified architecturally in PHASE1_Architecture_SRS.md section 15 but never
coded, since no prior phase (Portfolio Optimizer, Risk Management Engine)
ever needed to touch a concrete capital amount. Phase 6 is the first phase
whose entire job requires converting weights into quantities using a real
pool value - see PHASE6_Objectives.md section 0.1/section 0.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class ContributionSource(Enum):
    RECURRING_MONTHLY = "recurring_monthly"
    LUMP_SUM = "lump_sum"
    NONE = "none"


@dataclass(frozen=True)
class AvailableInvestmentPool:
    """The capital-agnostic abstraction from PHASE1_Architecture_SRS.md
    section 15.2. No module may reference an absolute rupee amount as a
    strategy PARAMETER - the only place an absolute amount may ever appear
    is as the current value of this pool, supplied at decision time by
    whatever queries the real Kite balance (CashLedgerPort, once Module 28
    exists; the Backtesting Engine's own Portfolio for backtests)."""

    existing_portfolio_value: float
    new_capital: float
    capital_source: ContributionSource
    as_of_date: date

    def __post_init__(self):
        if self.existing_portfolio_value < 0:
            raise ValueError(f"existing_portfolio_value cannot be negative, got {self.existing_portfolio_value}")
        if self.new_capital < 0:
            raise ValueError(f"new_capital cannot be negative, got {self.new_capital}")

    @property
    def total_investable(self):
        return self.existing_portfolio_value + self.new_capital


class FundingState(Enum):
    """The Monthly Funding Policy state machine, PHASE6_Objectives.md
    section 3.2. Persisted across daily invocations via StrategyStateStore,
    since Strategy Engine itself is stateless between calls (section 0.5/17)."""

    AWAITING_FUNDS = "awaiting_funds"
    EXECUTING = "executing"
    IDLE = "idle"


class Command(Enum):
    """Telegram Pause/Resume/Discontinue commands, PHASE6_Objectives.md
    section 10. Deliberately three distinct values, not a single
    duration-based command - Pause and Discontinue have different
    re-enable requirements and conflating them risks an accidentally
    permanent pause or an accidentally temporary discontinue."""

    PAUSE = "pause"
    RESUME = "resume"
    DISCONTINUE = "discontinue"


class RunMode(Enum):
    """Which invocation path is active. Both share the same core
    allocation logic (PHASE6_Objectives.md section 14) - this enum exists
    purely for audit-log clarity about which context produced a given
    order, not to branch decision logic."""

    BACKTEST = "backtest"
    LIVE_DAILY_CYCLE = "live_daily_cycle"


@dataclass(frozen=True)
class QueueEntrySummary:
    """Read-only view of an Investment Queue entry, as CashLedgerPort would
    expose it once Module 28 is implemented (PHASE1_Architecture_SRS.md
    section 16.5). Strategy Engine only ever reads this - it never writes
    to the queue directly, per section 16.10's binding rule."""

    queue_id: str
    deposit_date: date
    amount: float
    source: ContributionSource
    remaining_balance: float
    status: str


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    """What MarketIntelligencePort.get_market_regime() returns when Module
    27 has data - PHASE6_Objectives.md section 21.1. Strategy Engine may
    only ever use this as read-only advisory context (section 21.2); it
    must never appear inside any conditional in priority.py or the
    execution policies."""

    regime: str
    volatility_classification: str
    as_of_date: date


@dataclass(frozen=True)
class BuyOpportunity:
    """A single buy-only candidate produced by the priority-ordering step
    (PHASE6_Objectives.md section 5) - largest weight gap funded first."""

    symbol: str
    current_weight: float
    target_weight: float
    gap: float


@dataclass(frozen=True)
class ProposedOrderExplanation:
    """The human-readable rationale attached to every proposed order -
    same explainability standard as every prior phase."""

    symbol: str
    gap_at_decision_time: float
    priority_rank: int
    funding_source: ContributionSource
    market_context: str | None

    def as_text(self):
        base = (
            f"Buy-only allocation: {self.symbol} was {self.gap_at_decision_time:.1%} below target weight "
            f"(priority rank {self.priority_rank}), funded from a {self.funding_source.value} contribution."
        )
        if self.market_context:
            base += f" Market context (advisory only, not a factor in this decision): {self.market_context}"
        return base


@dataclass
class StrategyEngineState:
    """Strategy Engine's own persisted operational state - distinct from
    Module 28's cash ledger (which doesn't exist yet) and from Phase 4's
    backtest Portfolio (which is backtest-local). This is what survives
    between daily short-lived invocations (section 0.5/17): which month is
    active, what funding state that month is in, whether this month's
    reminder has already been sent, and when the funding workflow last ran."""

    current_month: str
    funding_state: FundingState
    reminder_sent_this_month: bool
    last_check_date: date | None
    paused: bool = False
    discontinued: bool = False


@dataclass(frozen=True)
class PendingSideEffects:
    """External calls (Telegram send, Investment Queue notification) that
    an ExecutionPolicy has DECIDED are needed, but has not yet performed.

    Found during the production verification review (see CHANGELOG.md):
    ExecutionPolicy previously called notification_port.send() and
    cash_ledger_port.notify_expected_contribution() directly, INSIDE
    run_cycle(), before the resulting state was ever persisted -- meaning
    a crash between the external call and the state save could cause a
    duplicate on restart. The fix is this class: run_cycle() now returns
    a description of what needs to happen, and the caller (strategy.py)
    persists the resulting state FIRST, then performs these effects
    afterward. This makes "every state transition is persisted before any
    external side effect" a structural property of the call sequence, not
    a hopefully-small crash window."""

    reminder_message: str | None = None
    expected_contribution: tuple | None = None  # (amount, expected_date, source) or None


@dataclass
class CycleResult:
    """The outcome of one run_daily_cycle() invocation - what gets
    persisted, logged, and (if applicable) turned into proposed orders.

    CRITICAL: `orders` are PROPOSED orders only (PHASE1_Architecture_SRS.md
    section 0.1a) -- they have NOT passed through the Approval Console and
    have NOT been verified against real-time cash by Module 28's
    verify_and_finalize(). Any caller intending to actually execute these
    must route them through both gates first. Strategy Engine itself
    never calls verify_and_finalize() -- that is the responsibility of
    whatever orchestrates the full approve-then-execute pipeline (Phase 9
    Scheduler / Phase 10-12), which sits outside Phase 6's scope. Treating
    `orders` as execution-ready is a misuse of this class, not a supported
    use case.

    CRASH-SAFETY (added after adversarial review, see CHANGELOG.md):
    the funding state does NOT advance to IDLE just because orders were
    computed. It stays EXECUTING until the caller explicitly calls
    confirm_cycle_outcome() after confirming successful downstream
    submission. `cycle_id` is a stable, deterministic identifier (derived
    from month + funding state, not from wall-clock time) the caller
    should use as an idempotency key when submitting to Module 28 -- a
    retried/regenerated proposal for the same unresolved cycle always
    carries the same cycle_id, and _build_buy_orders is a pure function of
    its inputs, so retrying after a crash reproduces the identical
    proposal rather than a different one.
    """

    as_of_date: date
    funding_state_before: FundingState
    funding_state_after: FundingState
    orders: list = field(default_factory=list)
    reminder_sent: bool = False
    notes: tuple = ()
    cycle_id: str | None = None
    deferred_to_next_trading_day: bool = False
