"""Exceptions raised by the Portfolio Cash & Execution Manager (Module 28)."""


class ExecutionManagerError(Exception):
    """Base class for all Module 28 errors."""


class ConcurrentInvocationError(ExecutionManagerError):
    """Raised when a second invocation attempts to claim a cycle already
    claimed by another in-flight invocation. Found during the Design
    Readiness Review (PHASE7_Design_Readiness_Review.md section 8.8) -
    mandatory reconciliation alone protects against crash-then-restart,
    not two overlapping invocations both deciding "nothing's submitted
    yet" at the same moment. This exception firing is the mutual-exclusion
    mechanism working correctly, not a bug."""


class DatabaseCorruptionError(ExecutionManagerError):
    """Raised when PRAGMA integrity_check fails on startup
    (PHASE7_Objectives.md section 8.9). Recovery is: treat the local
    database as equivalent to a fresh instance and rebuild state entirely
    from broker reconciliation - never attempt to repair or trust a
    corrupted file, since the broker is always the source of truth
    (Decision 1)."""


class NaiveDatetimeError(ExecutionManagerError):
    """Raised when a timezone-naive datetime is passed where an aware one
    is required (PHASE7_Objectives.md section 8.10). This is a hard,
    structural guard - not a lint warning - because a naive datetime
    silently mixed with an aware one is exactly the class of bug that
    passes every test that doesn't specifically probe for it."""


class InvalidLifecycleTransitionError(ExecutionManagerError):
    """Raised when code attempts to move an order to a state that isn't a
    valid transition from its current state. Fails loudly rather than
    silently accepting an inconsistent lifecycle."""


class BrokerCommunicationError(ExecutionManagerError):
    """Raised by BrokerPort implementations for any failure communicating
    with the broker (network, timeout, rate limit, server error, auth).
    Deliberately a single exception type at the BrokerPort boundary -
    callers handle broker failures uniformly regardless of underlying
    cause, consistent with the platform's existing retry.py pattern
    (Phase 2)."""
