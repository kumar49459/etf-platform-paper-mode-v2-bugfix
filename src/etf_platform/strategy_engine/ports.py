"""Abstract port interfaces for modules that do not exist in code yet
(Module 28, Module 13/Telegram, Module 26, Module 27) - PHASE6_Objectives.md
sections 0.3/0.4/21. Same dependency-inversion pattern already used for
DataProvider and SecretsProvider (Phase 2) and AllocationMethod (Phase 5):
Strategy Engine depends on these abstract interfaces, never on a concrete
implementation. Real implementations slot in later without Strategy
Engine's code changing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from etf_platform.strategy_engine.models import (
    AvailableInvestmentPool,
    Command,
    MarketRegimeSnapshot,
    QueueEntrySummary,
)


class CashLedgerPort(ABC):
    """Module 28 (Portfolio Cash & Execution Manager) - Phase 10/12 scope,
    per PHASE1_Architecture_SRS.md section 16.8. Strategy Engine only ever
    reads this and, for anticipated contributions, notifies it - it never
    spends cash or writes ledger entries directly (section 16.10)."""

    @abstractmethod
    def get_available_pool(self, as_of_date):
        """Query the actual Kite available (non-margin, non-collateral)
        cash balance - the sole source of truth per PHASE6_Objectives.md
        section 3.1. Must reflect real settled cash only."""

    @abstractmethod
    def get_pending_queue_entries(self):
        """Read-only view of the Investment Queue."""

    @abstractmethod
    def notify_expected_contribution(self, amount, expected_date, source):
        """Informational signal only - not a cash movement. See
        PHASE1_Architecture_SRS.md section 0.1a / PHASE6_Objectives.md
        section 3.2."""

    @abstractmethod
    def verify_and_finalize(self, proposed_orders):
        """Module 28's final-authority check (PHASE1_Architecture_SRS.md
        section 0.1a): may only reduce quantity, make price protection more
        conservative, or defer (return fewer orders) - never increase risk
        beyond what was proposed. This method's REAL implementation belongs
        to Module 28 (Phase 10); it is declared here only so Strategy
        Engine's calling code is written against the final interface shape
        from the start."""


class NotificationPort(ABC):
    """Module 13 (Telegram Notifications) - not yet implemented.
    PHASE6_Objectives.md section 10."""

    @abstractmethod
    def send(self, message):
        """Send a message. Per PHASE6_Objectives.md's edge cases, a
        NotificationPort failure must never block the underlying
        funding-check state machine - callers should treat this as
        best-effort, not gate execution on its success."""

    @abstractmethod
    def poll_commands(self):
        """Return any Pause/Resume/Discontinue commands received since the
        last poll."""


class OperationalEventPort(ABC):
    """Module 26 (Self-Healing Framework) - sequencing still open
    (PHASE1_Architecture_SRS.md section 14.6), unaffected by Phase 6.
    Strategy Engine emits events; it implements no self-healing logic
    itself (section 11 of PHASE6_Objectives.md)."""

    @abstractmethod
    def emit(self, event_type, details):
        """Record an operational event (cycle started/completed/failed,
        no-op cycle, proposal generated, notification delivery failure)."""


class MarketIntelligencePort(ABC):
    """Module 27 (Market Intelligence Engine) - a separate, later phase
    per PHASE1_Architecture_SRS.md section 18.6 item 2. Every method
    returns None as its NORMAL value when Module 27 is absent, disabled, or
    has no data yet - this is not an error condition. See
    PHASE6_Objectives.md section 21."""

    @abstractmethod
    def get_market_regime(self, as_of_date):
        ...

    @abstractmethod
    def get_relative_strength(self, symbol, as_of_date):
        ...

    @abstractmethod
    def get_sector_strength(self, sector, as_of_date):
        ...


class NullMarketIntelligencePort(MarketIntelligencePort):
    """Always returns None for every method - the DEFAULT test double for
    Strategy Engine's own test suite (PHASE6_Objectives.md section 21.3),
    not an edge case tested once. If Module 27 is simply not configured,
    this is also the correct real-world implementation to use."""

    def get_market_regime(self, as_of_date):
        return None

    def get_relative_strength(self, symbol, as_of_date):
        return None

    def get_sector_strength(self, sector, as_of_date):
        return None
