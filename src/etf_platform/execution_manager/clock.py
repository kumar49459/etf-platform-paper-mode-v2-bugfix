"""Controllable clock abstraction (Milestone 2, recommendation 3: avoid
real wall-clock dependency in tests - especially the long-duration
simulation covering hundreds/thousands of simulated trading days, which
must run in real seconds, not real days).

Deliberately NOT retrofitted onto Milestone 1's already-approved code
(ExecutionStateStore's claim/reconciliation timing) - Milestone 1 had no
defect requiring this, and per implementation rule 1, architecture is not
changed without a genuine reason. PaperBrokerPort (Milestone 2) has a
genuine, new need: simulating Delayed Fill scenarios and running long
simulations both require advancing time without waiting for it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from etf_platform.execution_manager.timezone_utils import require_aware, utc_now


class Clock(ABC):
    @abstractmethod
    def now(self):
        ...


class SystemClock(Clock):
    """The real clock - what KiteLiveBrokerPort (a later milestone) will
    use in production. Delegates to timezone_utils.utc_now(), the same
    UTC-only discipline established in Milestone 1."""

    def now(self):
        return utc_now()


class SimulatedClock(Clock):
    """Fully controllable - advances only when told to, never on its own.
    This is what makes "hundreds or thousands of consecutive trading days"
    (recommendation 6) something a test can run in real seconds."""

    def __init__(self, start=None):
        self._current = start if start is not None else utc_now()
        require_aware(self._current, "start")

    def now(self):
        return self._current

    def advance(self, delta):
        if delta.total_seconds() < 0:
            raise ValueError(f"SimulatedClock cannot move backward, got a negative delta: {delta}")
        self._current = self._current + delta

    def set(self, dt):
        require_aware(dt, "dt")
        self._current = dt
