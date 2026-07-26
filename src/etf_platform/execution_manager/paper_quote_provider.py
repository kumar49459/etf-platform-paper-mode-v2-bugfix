"""PaperQuoteProvider - the LiveQuoteProvider companion to PaperBrokerPort.
Kept as a separate class (not merged into PaperBrokerPort) because
LiveQuoteProvider and BrokerPort are deliberately separate interfaces -
Live Trading's real implementations will also be two separate classes, so
Paper Trading should exercise that same separation rather than collapsing
it for convenience.
"""

from __future__ import annotations

from etf_platform.execution_manager.events import ExecutionEvent, ExecutionEventType
from etf_platform.execution_manager.models import MarketDepthSnapshot
from etf_platform.execution_manager.ports import LiveQuoteProvider
from etf_platform.execution_manager.scenarios import BrokerScenario


class PaperQuoteProvider(LiveQuoteProvider):
    """CONSTRAINT WORTH STATING EXPLICITLY: if a ScenarioProvider is shared
    between this class and a PaperBrokerPort instance, use
    FixedScenarioProvider or SeededRandomScenarioProvider, never
    SequentialScenarioProvider -- the latter advances its internal index
    on every call regardless of caller, so interleaving quote calls with
    order-submission calls against a shared SequentialScenarioProvider
    would silently misassign scenarios between them. Tests that need
    SequentialScenarioProvider's determinism should give PaperBrokerPort
    and PaperQuoteProvider separate provider instances."""

    def __init__(self, clock, event_recorder, scenario_provider, base_prices):
        self._clock = clock
        self._events = event_recorder
        self._scenarios = scenario_provider
        self._base_prices = dict(base_prices)

    def get_last_traded_price(self, symbol):
        scenario, _ = self._scenarios.scenario_for(symbol, client_reference=f"quote-{symbol}")
        if scenario == BrokerScenario.QUOTE_UNAVAILABLE:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.QUOTE_UNAVAILABLE, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"call": "get_last_traded_price"},
            ))
            return None
        if symbol not in self._base_prices:
            return None
        return self._base_prices[symbol]

    def get_market_depth(self, symbol):
        scenario, _ = self._scenarios.scenario_for(symbol, client_reference=f"depth-{symbol}")
        if scenario == BrokerScenario.QUOTE_UNAVAILABLE:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.QUOTE_UNAVAILABLE, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"call": "get_market_depth"},
            ))
            return None
        if symbol not in self._base_prices:
            return None
        price = self._base_prices[symbol]
        return MarketDepthSnapshot(
            symbol=symbol, as_of=self._clock.now(), bid_price=round(price * 0.999, 2),
            ask_price=round(price * 1.001, 2), bid_quantity=1000, ask_quantity=1000,
        )
