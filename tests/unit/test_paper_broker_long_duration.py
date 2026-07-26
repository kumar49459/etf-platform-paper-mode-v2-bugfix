"""Long-duration simulation (recommendation 6) and performance measurement
(recommendation 7). The QUOTE_UNAVAILABLE stuck-order bug found during
this milestone's own long-duration testing (see paper_broker.py's
get_order_status() catch-all and CHANGELOG.md) was found by exactly this
kind of test -- kept here permanently, not just run once ad hoc.
"""

from __future__ import annotations

import time
import tracemalloc
import unittest
from datetime import timedelta

from etf_platform.execution_manager import (
    BrokerCommunicationError,
    ExecutionManagerError,
    InMemoryEventRecorder,
    OrderLifecycleState,
    OrderRejectedError,
    PaperBrokerPort,
    SeededRandomScenarioProvider,
    SimulatedClock,
    validate_transition,
)


class TestLongDurationSimulation(unittest.TestCase):
    NUM_DAYS = 500
    ORDERS_PER_DAY = 3

    def _run_simulation(self, purge_periodically):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = SeededRandomScenarioProvider(seed=12345)
        broker = PaperBrokerPort(clock, events, provider)

        stuck = 0
        memory_checkpoints = []
        tracemalloc.start()

        for day in range(self.NUM_DAYS):
            for order_num in range(self.ORDERS_PER_DAY):
                try:
                    oid = broker.submit_order(f"SYM{order_num}", "BUY", 100, 250.0, f"c-{day}-{order_num}")
                    last_state = OrderLifecycleState.PENDING
                    resolved = False
                    for _ in range(6):
                        try:
                            order = broker.get_order_status(oid)
                            if order.state != last_state:
                                validate_transition(last_state, order.state)
                                last_state = order.state
                            if order.state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED):
                                resolved = True
                                break
                        except (BrokerCommunicationError, ExecutionManagerError):
                            resolved = True
                            break
                    if not resolved:
                        stuck += 1
                except (OrderRejectedError, BrokerCommunicationError):
                    pass
            clock.advance(timedelta(days=1))
            if purge_periodically and day % 50 == 49:
                events.clear()
                broker.purge_terminal_orders()
                current, _ = tracemalloc.get_traced_memory()
                memory_checkpoints.append(current)

        tracemalloc.stop()
        return stuck, memory_checkpoints, broker

    def test_zero_orders_get_permanently_stuck(self):
        stuck, _, _ = self._run_simulation(purge_periodically=False)
        self.assertEqual(stuck, 0)

    def test_memory_stays_bounded_with_periodic_purging(self):
        _, checkpoints, _ = self._run_simulation(purge_periodically=True)
        self.assertGreaterEqual(len(checkpoints), 4, "Need enough checkpoints for a meaningful comparison.")
        first_half_avg = sum(checkpoints[: len(checkpoints) // 2]) / (len(checkpoints) // 2)
        second_half_avg = sum(checkpoints[len(checkpoints) // 2 :]) / (len(checkpoints) - len(checkpoints) // 2)
        growth_ratio = second_half_avg / first_half_avg
        self.assertLess(
            growth_ratio, 1.2,
            f"Memory grew {growth_ratio:.2f}x from first half to second half of the simulation.",
        )

    def test_all_orders_resolve_to_terminal_state_after_full_run(self):
        _, _, broker = self._run_simulation(purge_periodically=False)
        for order in broker._orders.values():
            self.assertIn(
                order.state, (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED),
                f"Order {order.broker_order_id} ended the simulation in non-terminal state {order.state}.",
            )

    def test_runs_in_real_seconds_not_real_days(self):
        start = time.perf_counter()
        self._run_simulation(purge_periodically=True)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"{self.NUM_DAYS} simulated days took {elapsed:.2f}s wall-clock -- too slow.")


class TestPerformanceMeasurement(unittest.TestCase):
    def test_dependency_weight_no_numpy_or_scipy(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; before=set(sys.modules); "
             "from etf_platform.execution_manager import PaperBrokerPort; "
             "after=set(sys.modules); new=after-before; "
             "print('numpy' if any('numpy' in m for m in new) else 'clean')"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.stdout.strip(), "clean")

    def test_throughput_is_adequate_for_realistic_daily_order_volume(self):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = SeededRandomScenarioProvider(seed=1)
        broker = PaperBrokerPort(clock, events, provider)

        start = time.perf_counter()
        for i in range(100):
            try:
                oid = broker.submit_order(f"SYM{i % 6}", "BUY", 10, 100.0, f"c-{i}")
                for _ in range(3):
                    try:
                        broker.get_order_status(oid)
                    except (BrokerCommunicationError, ExecutionManagerError):
                        break
            except (OrderRejectedError, BrokerCommunicationError):
                pass
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0, f"100 order lifecycles took {elapsed:.3f}s -- unexpectedly slow.")


if __name__ == "__main__":
    unittest.main()
