"""Abstract port interfaces for Module 28 (PHASE7_Objectives.md sections 3,
6.3, 8.4). Same dependency-inversion pattern used throughout this platform
since Phase 2 - Module 28's order-lifecycle core depends on these
interfaces only, never on a concrete implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerPort(ABC):
    """The core architectural move of this phase (PHASE7_Objectives.md
    section 3): ONE interface, satisfied identically by KiteLiveBrokerPort
    and PaperBrokerPort. Module 28's order-lifecycle state machine depends
    on this only - it has zero knowledge of which implementation is
    behind it, which is what makes Paper Trading a genuine rehearsal of
    Live Trading's exact code path, not just a similar one.
    """

    @abstractmethod
    def submit_order(self, symbol, side, quantity, limit_price, client_reference):
        """Submit a LIMIT order (this platform never submits MARKET
        orders). client_reference is the idempotency/dedup key (derived
        from cycle_id) - whether or not Kite honors it natively
        (unverified) is a KiteLiveBrokerPort implementation detail;
        PaperBrokerPort must accept and record it identically. Returns a
        broker_order_id."""

    @abstractmethod
    def get_order_status(self, broker_order_id):
        """Query the current status of a previously submitted order.
        Returns an OrderLifecycleState plus fill details if applicable."""

    @abstractmethod
    def cancel_order(self, broker_order_id):
        """Cancel a still-open order."""

    @abstractmethod
    def get_open_orders(self):
        """The reconciliation primitive (Decision 1, mandatory on every
        restart): the authoritative list of orders the broker currently
        considers open. Module 28's local records are always reconciled
        against this, never assumed correct on their own."""

    @abstractmethod
    def get_available_cash(self):
        """Real, live available (non-margin, non-collateral) cash -
        distinct from Strategy Engine's CashLedgerPort.get_available_pool(),
        which is Strategy Engine's own view; this is the live figure
        Module 28's verification stage checks immediately before
        submission."""


class LiveQuoteProvider(ABC):
    """New, additive interface - sits alongside the frozen DataProvider
    (Phase 2, built for historical OHLCV ingestion), never modifying it.
    Both KiteLiveBrokerPort and PaperBrokerPort consume real, live,
    read-only Kite quotes through this (Decision 2) - PaperBrokerPort
    never places real orders, but it does see real prices, which is what
    makes Paper Trading a realistic rehearsal rather than a synthetic
    one."""

    @abstractmethod
    def get_last_traded_price(self, symbol):
        ...

    @abstractmethod
    def get_market_depth(self, symbol):
        """Returns a MarketDepthSnapshot or None if depth data is
        unavailable - used for liquidity protection."""


class ComplianceCheckPort(ABC):
    """Found during the Design Readiness Review: even the two narrow
    inline checks approved for this phase (static IP verification, Algo
    ID tagging) sit behind this interface, not called directly from
    Module 28's core. Today's implementation (MinimalInlineComplianceChecker)
    satisfies this interface; Module 24's eventual real implementation will
    satisfy the same interface - Module 28's core verification logic never
    changes either way, only which implementation is wired in."""

    @abstractmethod
    def check(self, symbol, quantity, limit_price):
        """Returns a ComplianceResult. A FAIL result blocks submission -
        this is a hard gate, not advisory."""
