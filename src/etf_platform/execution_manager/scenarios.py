"""BrokerScenario framework (Milestone 2, recommendation 2). Rather than
randomly generating failures, PaperBrokerPort's behavior for any given
order is an explicit, named scenario - deterministic, reproducible, and
readable in a test's own code ("this order gets ORDER_REJECTED") rather
than inferred from a seed value.

Combined with recommendation 1 (determinism): a ScenarioProvider is the
injection point. FixedScenarioProvider and SequentialScenarioProvider are
fully deterministic with no randomness at all. SeededRandomScenarioProvider
exists specifically for the long-duration simulation (recommendation 6),
where hundreds of orders need *varied* but *reproducible* behavior - a
fixed seed makes a run repeatable even though individual scenario
assignment looks random.
"""

from __future__ import annotations

import random as _random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class BrokerScenario(Enum):
    IMMEDIATE_FILL = "immediate_fill"
    PARTIAL_FILL = "partial_fill"
    DELAYED_FILL = "delayed_fill"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXPIRED = "order_expired"
    NETWORK_TIMEOUT = "network_timeout"
    API_ERROR = "api_error"
    CONNECTION_LOST = "connection_lost"
    QUOTE_UNAVAILABLE = "quote_unavailable"


@dataclass(frozen=True)
class ScenarioParameters:
    """Tunable details for a scenario, so "partial fill" isn't just one
    fixed shape - e.g. what fraction fills, how many polls a delayed fill
    takes to resolve. All provisional defaults, disclosed as such, same
    honesty standard as every other provisional parameter in this
    platform."""

    partial_fill_ratio: float = 0.5
    delayed_fill_poll_count: int = 3
    rejection_reason: str = "Simulated rejection: insufficient simulated liquidity."


class ScenarioProvider(ABC):
    """The injection point (recommendation 1's "configurable scenario
    injection"). Given an order's identity, decides which BrokerScenario
    it exhibits. Every implementation here is fully deterministic given
    its configuration - no implementation of this class may call an
    unseeded random source."""

    @abstractmethod
    def scenario_for(self, symbol, client_reference):
        ...


class FixedScenarioProvider(ScenarioProvider):
    """Every order gets the same scenario - the simplest, most explicit
    case, for tests that want exactly one behavior under examination."""

    def __init__(self, scenario, parameters=None):
        self._scenario = scenario
        self._parameters = parameters or ScenarioParameters()

    def scenario_for(self, symbol, client_reference):
        return self._scenario, self._parameters


class SequentialScenarioProvider(ScenarioProvider):
    """A pre-configured list of scenarios, assigned in call order -
    useful for "the first order fills immediately, the second gets
    rejected" style tests. Deterministic: the Nth call always gets the
    Nth configured scenario, regardless of symbol or client_reference.
    Raises if called more times than scenarios were configured, rather
    than silently wrapping around or defaulting."""

    def __init__(self, scenarios):
        self._scenarios = list(scenarios)
        self._index = 0

    def scenario_for(self, symbol, client_reference):
        if self._index >= len(self._scenarios):
            raise IndexError(
                f"SequentialScenarioProvider exhausted after {len(self._scenarios)} configured scenarios -- "
                f"a {self._index + 1}th order was submitted with none configured for it. Configure enough "
                "scenarios for every order the test will submit, rather than relying on implicit reuse."
            )
        scenario, parameters = self._scenarios[self._index]
        self._index += 1
        return scenario, parameters


class SeededRandomScenarioProvider(ScenarioProvider):
    """For the long-duration simulation (recommendation 6): hundreds or
    thousands of orders need varied behavior, but the whole run must still
    be exactly reproducible from one seed value - this is what
    recommendation 1's "fixed random seed... fully repeatable test runs"
    means in practice. Uses Python's stdlib random.Random(seed), never the
    global random module, so two instances with the same seed are
    guaranteed identical regardless of what else in the process might
    have consumed random numbers."""

    def __init__(self, seed, weights=None):
        self._rng = _random.Random(seed)
        self._weights = weights or {
            BrokerScenario.IMMEDIATE_FILL: 0.55,
            BrokerScenario.PARTIAL_FILL: 0.15,
            BrokerScenario.DELAYED_FILL: 0.10,
            BrokerScenario.ORDER_REJECTED: 0.05,
            BrokerScenario.ORDER_CANCELLED: 0.05,
            BrokerScenario.ORDER_EXPIRED: 0.05,
            BrokerScenario.NETWORK_TIMEOUT: 0.02,
            BrokerScenario.API_ERROR: 0.01,
            BrokerScenario.CONNECTION_LOST: 0.01,
            BrokerScenario.QUOTE_UNAVAILABLE: 0.01,
        }
        total = sum(self._weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Scenario weights must sum to 1.0, got {total}")

    def scenario_for(self, symbol, client_reference):
        scenarios = list(self._weights.keys())
        weights = list(self._weights.values())
        chosen = self._rng.choices(scenarios, weights=weights, k=1)[0]
        return chosen, ScenarioParameters()
