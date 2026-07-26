"""Structured event recording (Milestone 2, recommendation 4). Every
PaperBrokerPort action emits a structured event - this is what makes
debugging and verification tractable, especially for the long-duration
simulation (recommendation 6) where reading raw state alone wouldn't show
*how* an order got from PROPOSAL to wherever it ended up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from etf_platform.execution_manager.timezone_utils import require_aware


class ExecutionEventType(Enum):
    ORDER_SUBMITTED = "order_submitted"
    ORDER_PENDING = "order_pending"
    PARTIAL_FILL = "partial_fill"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    ORDER_EXPIRED = "order_expired"
    API_TIMEOUT = "api_timeout"
    NETWORK_ERROR = "network_error"
    API_ERROR = "api_error"
    QUOTE_UNAVAILABLE = "quote_unavailable"
    RECONCILIATION_CHECK = "reconciliation_check"


@dataclass(frozen=True)
class ExecutionEvent:
    """Extended for Milestone 3 (recommendation 6): correlation_id,
    cycle_id, component, and result are new, optional, default-valued
    fields -- additive, not a breaking change to Milestone 2's existing
    construction calls (PaperBrokerPort, PaperQuoteProvider), which don't
    set them and don't need to. Module 28 as a whole is still
    pre-release/unfrozen, so extending an already-committed model
    additively within the same module is consistent with how Phase 6
    extended its own models mid-implementation before freezing -- this
    is not a change to anything tagged or frozen."""

    event_type: ExecutionEventType
    timestamp: object
    broker_order_id: object
    symbol: object
    details: dict = field(default_factory=dict)
    correlation_id: object = None
    cycle_id: object = None
    component: object = None
    result: object = None

    def __post_init__(self):
        require_aware(self.timestamp, "timestamp")


class EventRecorder(ABC):
    @abstractmethod
    def record(self, event):
        ...

    @abstractmethod
    def events(self):
        """All recorded events, in emission order."""

    @abstractmethod
    def events_for_order(self, broker_order_id):
        ...


class InMemoryEventRecorder(EventRecorder):
    """Fast, simple - the default for unit tests that don't need
    persistence. NOT suitable on its own for the long-duration simulation
    (recommendation 6), where an unbounded in-memory list across thousands
    of simulated days would itself be exactly the kind of resource-growth
    issue that simulation exists to catch."""

    def __init__(self):
        self._events = []

    def record(self, event):
        self._events.append(event)

    def events(self):
        return list(self._events)

    def events_for_order(self, broker_order_id):
        return [e for e in self._events if e.broker_order_id == broker_order_id]

    def clear(self):
        """Explicit, deliberate - never automatic. A long-duration
        simulation that wants bounded memory calls this itself between
        measurement checkpoints; InMemoryEventRecorder never silently
        drops events on its own, since silent data loss would defeat the
        entire point of event recording."""
        self._events.clear()
