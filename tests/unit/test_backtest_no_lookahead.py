"""The single most important test file in Phase 4: proves the backtesting
engine structurally cannot look ahead. If any test here fails, the engine
is unsafe to use for any real decision, regardless of what else works.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.backtesting import BacktestConfig, BacktestEngine, OrderIntent, OrderType, Strategy
from etf_platform.backtesting.exceptions import LookAheadViolationError
from etf_platform.cost_tax_engine import Side
from etf_platform.data_engine.models import OHLCVBar


def make_bars(closes: list[float], start: date) -> list[OHLCVBar]:
    return [
        OHLCVBar("X", start + timedelta(days=i), c - 0.5, c + 0.5, c - 1, c, 100000)
        for i, c in enumerate(closes)
    ]


class RecordingStrategy(Strategy):
    """Records exactly what history it was shown on every call, so the test
    can assert against it directly — this is what makes the no-look-ahead
    guarantee testable rather than just inspectable by reading code."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, dict[str, list[OHLCVBar]]]] = []

    def generate_orders(self, as_of_date, history, portfolio):
        # Deep-ish copy of the relevant info to survive engine mutation.
        self.calls.append((as_of_date, {s: list(bars) for s, bars in history.items()}))
        return []


class TestNoLookAheadStructuralGuarantee(unittest.TestCase):
    def setUp(self) -> None:
        self.closes = [100 + i * 0.3 for i in range(60)]
        self.bars = {"X": make_bars(self.closes, date(2025, 1, 1))}
        self.config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 2, 20),
            initial_capital=100000, symbols=("X",),
        )

    def test_strategy_never_sees_a_bar_dated_after_as_of_date(self) -> None:
        strategy = RecordingStrategy()
        engine = BacktestEngine(self.config, strategy)
        engine.run(self.bars)

        for as_of_date, history in strategy.calls:
            for symbol, bars in history.items():
                for bar in bars:
                    self.assertLessEqual(
                        bar.trade_date, as_of_date,
                        f"Strategy was shown a bar dated {bar.trade_date} on call as_of_date={as_of_date} "
                        f"— this is a look-ahead violation.",
                    )

    def test_strategy_sees_exactly_the_bar_for_its_own_as_of_date(self) -> None:
        """Confirms the boundary is inclusive (today's own close IS visible
        today) — not off-by-one in the conservative direction either."""
        strategy = RecordingStrategy()
        engine = BacktestEngine(self.config, strategy)
        engine.run(self.bars)

        last_call_date, last_history = strategy.calls[-1]
        dates_seen = {b.trade_date for b in last_history["X"]}
        self.assertIn(last_call_date, dates_seen)

    def test_order_decided_at_t_fills_no_earlier_than_t_plus_1(self) -> None:
        class BuyOnFirstCall(Strategy):
            def __init__(self):
                self.done = False

            def generate_orders(self, as_of_date, history, portfolio):
                if not self.done:
                    self.done = True
                    return [OrderIntent("X", Side.BUY, OrderType.MARKET, 10, "test buy")]
                return []

        engine = BacktestEngine(self.config, BuyOnFirstCall())
        result = engine.run(self.bars)

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertGreater(trade.fill.fill_date, trade.fill.decided_date)
        self.assertEqual(trade.fill.fill_date, trade.fill.decided_date + timedelta(days=1))

    def test_fill_price_is_next_bar_open_not_decision_bar_close(self) -> None:
        """The classic look-ahead bug this whole design exists to prevent:
        deciding on bar T's close and filling at that SAME price. Assert
        the fill price matches the NEXT bar's open, which is a different
        number than the decision bar's close in this fixture (closes rise
        by 0.3/day, opens are close-0.5, so decision-bar-close != next-bar-open)."""
        class BuyOnFirstCall(Strategy):
            def __init__(self):
                self.done = False

            def generate_orders(self, as_of_date, history, portfolio):
                if not self.done:
                    self.done = True
                    return [OrderIntent("X", Side.BUY, OrderType.MARKET, 10, "test buy")]
                return []

        engine = BacktestEngine(self.config, BuyOnFirstCall())
        result = engine.run(self.bars)
        trade = result.trades[0]

        decision_bar = next(b for b in self.bars["X"] if b.trade_date == trade.fill.decided_date)
        fill_bar = next(b for b in self.bars["X"] if b.trade_date == trade.fill.fill_date)

        self.assertNotEqual(trade.fill.fill_price, decision_bar.close)
        self.assertEqual(trade.fill.fill_price, fill_bar.open)

    def test_lookahead_violation_error_would_fire_if_invariant_broken(self) -> None:
        """Directly exercises the defensive runtime assertion in
        _build_history_view by constructing a pathological scenario where
        the pointer bookkeeping is deliberately corrupted, proving the
        check is live code, not dead code that never actually runs."""
        from etf_platform.backtesting.engine import BacktestEngine as EngineClass

        engine = EngineClass(self.config, RecordingStrategy())
        # Directly call the internal method with a pointer that has
        # over-advanced past current_date, simulating a hypothetical future
        # bug in the pointer-advance logic.
        bad_symbol_bars = {"X": self.bars["X"]}
        bad_symbol_dates = {"X": [b.trade_date for b in self.bars["X"]]}
        bad_pointer = {"X": 5}  # claims 5 bars observed, but current_date is before bar[4]'s date
        with self.assertRaises(LookAheadViolationError):
            engine._build_history_view(bad_symbol_bars, bad_symbol_dates, bad_pointer, date(2025, 1, 1))

    def test_lookback_window_respects_configured_days(self) -> None:
        strategy = RecordingStrategy()
        short_lookback_config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 2, 20),
            initial_capital=100000, symbols=("X",), lookback_days_provided_to_strategy=10,
        )
        engine = BacktestEngine(short_lookback_config, strategy)
        engine.run(self.bars)

        last_date, last_history = strategy.calls[-1]
        oldest_bar_date = min(b.trade_date for b in last_history["X"])
        self.assertGreaterEqual((last_date - oldest_bar_date).days, 0)
        self.assertLessEqual((last_date - oldest_bar_date).days, 10)

    def test_warmup_bars_before_start_date_never_trigger_strategy_calls(self) -> None:
        """Bars dated before config.start_date should build lookback history
        but must never themselves trigger a strategy call or appear as an
        equity curve point."""
        strategy = RecordingStrategy()
        config = BacktestConfig(
            start_date=date(2025, 1, 20), end_date=date(2025, 2, 20),
            initial_capital=100000, symbols=("X",),
        )
        engine = BacktestEngine(config, strategy)
        result = engine.run(self.bars)

        for as_of_date, _ in strategy.calls:
            self.assertGreaterEqual(as_of_date, config.start_date)
        for point in result.equity_curve:
            self.assertGreaterEqual(point.as_of_date, config.start_date)
        # But lookback history should still include pre-start bars.
        first_call_date, first_history = strategy.calls[0]
        earliest_seen = min(b.trade_date for b in first_history["X"])
        self.assertLess(earliest_seen, config.start_date)


if __name__ == "__main__":
    unittest.main()
