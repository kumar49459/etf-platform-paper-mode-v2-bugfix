"""Generic failure injection (Milestone 4, requirement 2: expand beyond
BrokerScenario's order-lifecycle-specific failures to every external
dependency Module 28 touches). Deterministic and seeded, same discipline
as Milestone 2's BrokerScenario framework - a failure that occurs is
reproducible, not a flake.

Honest scope note on "Configuration loading": Module 28 doesn't have its
own configuration-loading subsystem distinct from Phase 2's SecretsManager
(frozen, used for credentials) - there's no dedicated "config file" this
module reads. The closest analogue is VerificationService's threshold
parameters (max_spread_pct etc.), which is what the CONFIG_LOADING
category below targets via FailureInjectingComplianceChecker. This is a
best-effort mapping onto a category the architecture doesn't have a
dedicated component for yet, not a claim that a real config-loading
subsystem was tested.
"""

from __future__ import annotations

import random
from enum import Enum

from etf_platform.execution_manager.exceptions import BrokerCommunicationError, DatabaseCorruptionError


class DependencyCategory(Enum):
    BROKER = "broker"
    DATABASE = "database"
    NETWORK = "network"
    QUOTE_PROVIDER = "quote_provider"
    NOTIFICATION = "notification"
    CONFIG_LOADING = "config_loading"
    STORAGE = "storage"


def _exception_for(category, detail):
    if category == DependencyCategory.BROKER:
        return BrokerCommunicationError(f"Injected broker failure: {detail}")
    if category in (DependencyCategory.DATABASE, DependencyCategory.STORAGE):
        return DatabaseCorruptionError(f"Injected database/storage failure: {detail}")
    if category == DependencyCategory.NETWORK:
        return ConnectionError(f"Injected network failure: {detail}")
    if category == DependencyCategory.QUOTE_PROVIDER:
        return BrokerCommunicationError(f"Injected quote provider failure: {detail}")
    if category == DependencyCategory.NOTIFICATION:
        return RuntimeError(f"Injected notification failure: {detail}")
    if category == DependencyCategory.CONFIG_LOADING:
        return RuntimeError(f"Injected config-loading failure: {detail}")
    return RuntimeError(f"Injected failure ({category}): {detail}")


class FailureInjector:
    """Wraps random.Random(seed) directly (never the global random
    module) - two injectors with the same seed produce identical failure
    sequences regardless of what else in the process has consumed random
    numbers, same determinism guarantee as SeededRandomScenarioProvider."""

    def __init__(self, seed, failure_rate=0.02):
        self._rng = random.Random(seed)
        self._failure_rate = failure_rate
        self.injection_log = []

    def maybe_fail(self, category, detail=""):
        if self._rng.random() < self._failure_rate:
            self.injection_log.append((category, detail))
            raise _exception_for(category, detail)


class FailureInjectingStore:
    """Wraps ExecutionStateStore. claim_cycle() failures are deliberately
    NOT injected here -- a failed claim attempt has completely different
    safety implications than a failed save/load and deserves separate,
    deliberate treatment, not accidental coverage via a generic wrapper."""

    def __init__(self, real_store, injector):
        self._store = real_store
        self._injector = injector

    def save_execution_record(self, record):
        self._injector.maybe_fail(DependencyCategory.DATABASE, f"save {record.execution_id}")
        return self._store.save_execution_record(record)

    def load_execution_record(self, execution_id):
        self._injector.maybe_fail(DependencyCategory.DATABASE, f"load {execution_id}")
        return self._store.load_execution_record(execution_id)

    def load_records_for_cycle(self, cycle_id):
        self._injector.maybe_fail(DependencyCategory.DATABASE, f"load_records_for_cycle {cycle_id}")
        return self._store.load_records_for_cycle(cycle_id)

    def load_unresolved_records(self):
        self._injector.maybe_fail(DependencyCategory.DATABASE, "load_unresolved_records")
        return self._store.load_unresolved_records()

    def claim_cycle(self, *args, **kwargs):
        return self._store.claim_cycle(*args, **kwargs)

    def release_cycle(self, *args, **kwargs):
        return self._store.release_cycle(*args, **kwargs)

    def is_claimed(self, *args, **kwargs):
        return self._store.is_claimed(*args, **kwargs)

    def close(self):
        return self._store.close()


class FailureInjectingNotifier:
    def __init__(self, real_notifier, injector):
        self._notifier = real_notifier
        self._injector = injector

    def send(self, message):
        self._injector.maybe_fail(DependencyCategory.NOTIFICATION, message[:40])
        return self._notifier.send(message)

    def poll_commands(self):
        return self._notifier.poll_commands()


class FailureInjectingQuoteProvider:
    """Beyond BrokerScenario.QUOTE_UNAVAILABLE (which returns None, a
    normal/expected outcome per the port's own contract) - this injects a
    genuine, unexpected exception, simulating the quote provider itself
    being unreachable rather than merely having no data for a symbol."""

    def __init__(self, real_provider, injector):
        self._provider = real_provider
        self._injector = injector

    def get_last_traded_price(self, symbol):
        self._injector.maybe_fail(DependencyCategory.QUOTE_PROVIDER, f"get_last_traded_price {symbol}")
        return self._provider.get_last_traded_price(symbol)

    def get_market_depth(self, symbol):
        self._injector.maybe_fail(DependencyCategory.QUOTE_PROVIDER, f"get_market_depth {symbol}")
        return self._provider.get_market_depth(symbol)


class FailureInjectingComplianceChecker:
    """Stand-in for "configuration loading" failures - see module
    docstring's honest scope note."""

    def __init__(self, real_checker, injector):
        self._checker = real_checker
        self._injector = injector

    def check(self, symbol, quantity, limit_price):
        self._injector.maybe_fail(DependencyCategory.CONFIG_LOADING, f"compliance config for {symbol}")
        return self._checker.check(symbol, quantity, limit_price)
