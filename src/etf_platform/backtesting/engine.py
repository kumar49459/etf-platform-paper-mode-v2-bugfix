"""Event-driven Backtesting Engine (Phase 4).

THE NO-LOOK-AHEAD SEQUENCING, spelled out explicitly since this is the
single most important correctness property this module has (Phase 4
objectives #2, #4):

For each trading date T, in this exact order:
  1. Advance each symbol's "observed bars" pointer to include T's bar (T's
     own close is legitimately known at the end of day T).
  2. Process any corporate actions (dividends, splits/bonus) with ex_date
     == T, using the position held as of the start of T.
  3. Attempt to fill any orders that were queued on an EARLIER date, using
     T's bar. An order decided on date D is never eligible to fill before
     D+1 -- see `_queue_orders()`.
  4. Record the equity curve point for T, using T's close prices (now
     legitimately known), warning if any held position's price data has
     gone stale (see `_check_stale_prices`, added during the Phase 4
     adversarial review -- see CHANGELOG.md).
  5. Build the history view for the Strategy: bars with trade_date <= T
     only (enforced by construction -- the pointer from step 1 never
     exposes a later bar). Call `strategy.generate_orders(T, history, ...)`.
  6. Queue any new orders from step 5 for earliest fill on T+1 -- never T.

Steps 3 and 5 are separated by step 4 specifically so that a Strategy's
decision at T can never be filled at T -- there is no code path where an
order decided on T's bar is matched against T's own bar. This is verified
by tests/unit/test_backtest_no_lookahead.py, not just asserted here.

THREAD SAFETY: BacktestEngine, Portfolio, and CostTaxEngine are NOT
thread-safe and are not designed to be -- each holds simple mutable
instance state (cash, positions, FIFO lots) with no locking, by design,
since a single backtest run is an inherently sequential simulation (each
day's decisions depend on the prior day's portfolio state). One instance
of each must be used by exactly one thread for the lifetime of one
`run()` call. Running multiple INDEPENDENT backtests concurrently (e.g.
parallelizing WalkForwardValidator's windows) is safe as long as each gets
its own BacktestEngine/Portfolio/CostTaxEngine instance -- never share one
across threads. This is a different concurrency model from
BacktestRunRegistry (Phase 2's WAL+lock pattern, safe for concurrent use
within one process) and from the live-trading components (Phase 1's
single-writer-per-domain principle) -- each layer of this platform uses
the concurrency model appropriate to what it actually does, not one
uniform policy applied everywhere.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta

from etf_platform.backtesting.exceptions import InvalidOrderError, LookAheadViolationError
from etf_platform.backtesting.fill_simulator import FillSimulator
from etf_platform.backtesting.models import (
    BacktestConfig,
    BacktestResult,
    CorporateActionEvent,
    DividendReceipt,
    EquityCurvePoint,
    OrderIntent,
    OrderType,
    PendingOrder,
    PortfolioSnapshot,
    Trade,
)
from etf_platform.backtesting.portfolio import InsufficientCashError, OverSellError, Portfolio
from etf_platform.backtesting.strategy import Strategy
from etf_platform.common.logging_setup import get_logger
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar

logger = get_logger("backtesting.engine")


class BacktestEngine:
    def __init__(
        self,
        config: BacktestConfig,
        strategy: Strategy,
        cost_tax_engine: CostTaxEngine | None = None,
    ) -> None:
        self._config = config
        self._strategy = strategy
        self._cost_tax_engine = cost_tax_engine or CostTaxEngine()
        self._fill_simulator = FillSimulator(self._cost_tax_engine, config.max_volume_participation_pct)
        self._portfolio = Portfolio(config.initial_capital, self._cost_tax_engine)

    @property
    def config(self) -> BacktestConfig:
        return self._config

    def run(
        self,
        bars_by_symbol: dict[str, list[OHLCVBar]],
        corporate_actions_by_symbol: dict[str, list[CorporateAction]] | None = None,
    ) -> BacktestResult:
        """`bars_by_symbol` should already include any lookback history the
        Strategy needs before `config.start_date` (fetched with extra lead
        time by the caller) -- bars dated before start_date build the
        Strategy's initial history view and never generate fills or equity
        curve points themselves.

        `corporate_actions_by_symbol`, if provided, is processed by ex_date:
        DIVIDEND credits cash based on quantity held; SPLIT/BONUS adjusts
        held quantity and FIFO tax lots (preserving original acquisition
        dates -- see CostTaxEngine.apply_split). MERGER/OTHER are logged as
        an explicit warning, not silently ignored -- this platform does not
        yet model those event types, and pretending otherwise would be
        worse than flagging the gap.
        """
        corporate_actions_by_symbol = corporate_actions_by_symbol or {}
        self._validate_bars_sanity(bars_by_symbol)
        ca_by_symbol_date: dict[str, dict[date, list[CorporateAction]]] = {}
        for symbol, actions in corporate_actions_by_symbol.items():
            for ca in actions:
                ca_by_symbol_date.setdefault(symbol, {}).setdefault(ca.ex_date, []).append(ca)

        symbol_bars = {s: sorted(bars, key=lambda b: b.trade_date) for s, bars in bars_by_symbol.items()}
        symbol_dates = {s: [b.trade_date for b in bars] for s, bars in symbol_bars.items()}
        bar_by_symbol_date = {s: {b.trade_date: b for b in bars} for s, bars in symbol_bars.items()}
        pointer = {s: 0 for s in symbol_bars}

        all_dates = sorted(
            {b.trade_date for bars in symbol_bars.values() for b in bars if b.trade_date <= self._config.end_date}
        )

        pending_orders: list[PendingOrder] = []
        trades: list[Trade] = []
        rejected_orders: list[tuple[OrderIntent, str]] = []
        equity_curve: list[EquityCurvePoint] = []
        warnings: list[str] = []
        dividend_receipts: list[DividendReceipt] = []
        corporate_action_events: list[CorporateActionEvent] = []
        last_known_price: dict[str, float] = {}
        last_price_date: dict[str, date] = {}
        warned_stale_at: dict[str, int] = {}  # symbol -> staleness_days already warned about

        for current_date in all_dates:
            # Step 1: advance observed-bar pointers.
            for symbol, bars in symbol_bars.items():
                while pointer[symbol] < len(bars) and bars[pointer[symbol]].trade_date <= current_date:
                    last_known_price[symbol] = bars[pointer[symbol]].close
                    last_price_date[symbol] = bars[pointer[symbol]].trade_date
                    pointer[symbol] += 1

            if current_date < self._config.start_date:
                continue  # pure lookback warm-up: history accumulates, nothing else happens yet

            # Step 2: corporate actions with ex_date == current_date.
            for symbol, date_map in ca_by_symbol_date.items():
                for ca in date_map.get(current_date, []):
                    if ca.action_type == CorporateActionType.DIVIDEND:
                        receipt = self._portfolio.apply_dividend(symbol, current_date, ca.ratio_or_amount)
                        if receipt is not None:
                            dividend_receipts.append(receipt)
                    elif ca.action_type in (CorporateActionType.SPLIT, CorporateActionType.BONUS):
                        event = self._portfolio.apply_split(symbol, current_date, ca.ratio_or_amount)
                        if event is not None:
                            corporate_action_events.append(event)
                    else:
                        msg = (
                            f"Unhandled corporate action type '{ca.action_type.value}' for {symbol} on "
                            f"{current_date} -- not processed. Portfolio state does not reflect this event."
                        )
                        logger.warning(msg)
                        warnings.append(msg)

            # Step 3: attempt fills for orders queued on an earlier date.
            pending_orders = self._process_pending_orders(
                pending_orders, current_date, bar_by_symbol_date, trades, rejected_orders
            )

            # Step 4: equity curve point using today's (now-known) prices, with staleness check.
            self._check_stale_prices(current_date, last_price_date, warned_stale_at, warnings)
            equity_curve.append(self._portfolio.equity_curve_point(current_date, dict(last_known_price)))

            # Step 5: build the Strategy's history view -- bars <= current_date only.
            history = self._build_history_view(symbol_bars, symbol_dates, pointer, current_date)
            portfolio_snapshot = PortfolioSnapshot(
                as_of_date=current_date,
                cash=self._portfolio.cash,
                positions=self._portfolio.positions(),
                total_value=equity_curve[-1].total_value,
            )
            new_intents = self._strategy.generate_orders(current_date, history, portfolio_snapshot)

            # Step 6: queue new orders for earliest fill on the NEXT date, never today.
            for intent in new_intents:
                pending_orders.append(self._queue_order(intent, current_date))

        self._reject_unfilled_orders_at_end(pending_orders, rejected_orders)

        actual_end_date = all_dates[-1] if all_dates else None
        if not all_dates:
            msg = "No trading dates found in the provided data for the configured date range."
            logger.warning(msg)
            warnings.append(msg)
        elif actual_end_date < self._config.end_date:
            msg = (
                f"Backtest data ended on {actual_end_date}, before the configured end_date "
                f"{self._config.end_date} -- no further bars were available for any symbol. "
                f"Results only cover through {actual_end_date}; treat this as an early, incomplete run, "
                "not a full-period result."
            )
            logger.warning(msg)
            warnings.append(msg)

        return BacktestResult(
            config=self._config,
            equity_curve=equity_curve,
            trades=trades,
            rejected_orders=rejected_orders,
            warnings=warnings,
            actual_end_date=actual_end_date,
            dividend_receipts=dividend_receipts,
            corporate_action_events=corporate_action_events,
        )

    @staticmethod
    def _validate_bars_sanity(bars_by_symbol: dict[str, list[OHLCVBar]]) -> None:
        """A lightweight defensive gate -- NOT a replacement for the Data
        Quality Validator's full check pipeline (Phase 2). Its purpose is
        narrower: catch the case where a caller feeds obviously invalid
        data (non-positive prices) directly into the engine, bypassing
        validation entirely, which would otherwise silently produce
        nonsensical cost/tax/equity numbers with no error raised anywhere.
        Per Phase 1's "nothing downstream reads raw data directly"
        principle, the Backtesting Engine IS downstream and must not
        assume a caller remembered to validate. Found during the Phase 4
        adversarial review (see CHANGELOG.md).
        """
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
                    raise InvalidOrderError(
                        f"Non-positive price in OHLCV data for {symbol} on {bar.trade_date} "
                        f"(open={bar.open}, high={bar.high}, low={bar.low}, close={bar.close}). "
                        "The Backtesting Engine requires data that has already passed the Data "
                        "Quality Validator (Phase 2) -- run ingestion/validation first, don't feed "
                        "raw provider data directly into a backtest."
                    )
                if bar.low > bar.high:
                    raise InvalidOrderError(
                        f"Invalid OHLCV bar for {symbol} on {bar.trade_date}: low ({bar.low}) > "
                        f"high ({bar.high}). This data has not passed basic quality checks."
                    )

    def _check_stale_prices(
        self,
        current_date: date,
        last_price_date: dict[str, date],
        warned_stale_at: dict[str, int],
        warnings: list[str],
    ) -> None:
        """Warn when a HELD position's price data has gone stale (no new
        bar for `stale_price_warning_days` or more) -- previously this was
        silently carried forward forever with no ongoing signal (Phase 4
        adversarial-review finding, see CHANGELOG.md). Warns once when the
        threshold is first crossed, then again every ~30 days it persists,
        rather than every single day (which would be unusably noisy for a
        long-running gap)."""
        for symbol in self._config.symbols:
            held = self._portfolio.position(symbol)
            if held <= 1e-9:
                continue
            price_date = last_price_date.get(symbol)
            if price_date is None:
                continue
            staleness_days = (current_date - price_date).days
            if staleness_days < self._config.stale_price_warning_days:
                continue
            already_warned_at = warned_stale_at.get(symbol, -1)
            if staleness_days == self._config.stale_price_warning_days or (
                staleness_days - already_warned_at >= 30
            ):
                msg = (
                    f"Held position '{symbol}' ({held:.4f} units) has had no price update for "
                    f"{staleness_days} days as of {current_date} (last known price date: {price_date}). "
                    "Valuation may be stale/unreliable -- check for a data gap or delisting."
                )
                logger.warning(msg)
                warnings.append(msg)
                warned_stale_at[symbol] = staleness_days

    def _process_pending_orders(
        self,
        pending_orders: list[PendingOrder],
        current_date: date,
        bar_by_symbol_date: dict[str, dict[date, OHLCVBar]],
        trades: list[Trade],
        rejected_orders: list[tuple[OrderIntent, str]],
    ) -> list[PendingOrder]:
        still_pending: list[PendingOrder] = []
        for pending in pending_orders:
            if current_date < pending.target_fill_date:
                still_pending.append(pending)
                continue

            bar = bar_by_symbol_date.get(pending.intent.symbol, {}).get(current_date)
            if bar is None:
                if current_date <= pending.expiry_date:
                    still_pending.append(pending)
                else:
                    rejected_orders.append(
                        (pending.intent, f"Expired unfilled by {pending.expiry_date}: no trading data available.")
                    )
                continue

            fill = self._fill_simulator.try_fill(pending, bar)
            if fill is None:
                if current_date < pending.expiry_date:
                    still_pending.append(pending)
                else:
                    rejected_orders.append(
                        (pending.intent, f"Limit price never touched before expiry {pending.expiry_date}.")
                    )
                continue

            if fill.side == Side.BUY and not self._portfolio.can_afford_buy(fill):
                rejected_orders.append((pending.intent, "Insufficient cash at fill time."))
                continue
            if fill.side == Side.SELL and not self._portfolio.can_afford_sell(fill):
                rejected_orders.append((pending.intent, "Attempted to sell more than currently held at fill time."))
                continue

            try:
                trade = self._portfolio.apply_fill(fill)
                trades.append(trade)
            except (InsufficientCashError, OverSellError) as exc:
                rejected_orders.append((pending.intent, str(exc)))
                continue

            # Partial fill (volume participation cap): re-queue the unfilled
            # remainder, but only if it hasn't expired -- previously this
            # silently ignored expiry_date for partial fills (an order could
            # keep trickling in forever regardless of the configured expiry,
            # found via adversarial testing) and the rationale string grew
            # by one appended phrase per partial fill, unboundedly, across
            # many days (a cosmetic but real bug -- see CHANGELOG.md).
            remainder = pending.intent.quantity - fill.quantity
            if remainder > 1e-9:
                if current_date < pending.expiry_date:
                    # Reuse the ORIGINAL rationale unchanged -- the reason
                    # for the trade hasn't changed just because it's
                    # spanning multiple days; the multi-day nature is
                    # already visible via separate Trade records with
                    # distinct fill_dates, so nothing is lost by not
                    # mutating the text on every continuation.
                    remainder_intent = OrderIntent(
                        symbol=pending.intent.symbol, side=pending.intent.side,
                        order_type=pending.intent.order_type, quantity=remainder,
                        rationale=pending.intent.rationale, limit_price=pending.intent.limit_price,
                    )
                    still_pending.append(
                        PendingOrder(
                            intent=remainder_intent, decided_date=pending.decided_date,
                            target_fill_date=current_date + timedelta(days=1), expiry_date=pending.expiry_date,
                        )
                    )
                else:
                    rejected_orders.append(
                        (
                            pending.intent,
                            f"Partially filled {fill.quantity:.4f} of {pending.intent.quantity:.4f} units; "
                            f"remaining {remainder:.4f} units expired unfilled by {pending.expiry_date} "
                            "(volume participation cap limited daily fill size).",
                        )
                    )

        return still_pending

    def _build_history_view(
        self,
        symbol_bars: dict[str, list[OHLCVBar]],
        symbol_dates: dict[str, list[date]],
        pointer: dict[str, int],
        current_date: date,
    ) -> dict[str, list[OHLCVBar]]:
        cutoff = current_date - timedelta(days=self._config.lookback_days_provided_to_strategy)
        history: dict[str, list[OHLCVBar]] = {}
        for symbol, bars in symbol_bars.items():
            end_idx = pointer[symbol]
            if end_idx and bars[end_idx - 1].trade_date > current_date:
                raise LookAheadViolationError(
                    f"Internal invariant violated: observed bar for {symbol} dated "
                    f"{bars[end_idx - 1].trade_date} is after current simulation date {current_date}."
                )
            # bisect on the full sorted date list to find the cutoff start
            # index directly, rather than slicing/filtering the entire
            # observed history on every call -- for a multi-year backtest
            # this turns an O(elapsed_days) per-call cost into O(log n +
            # window_size), a meaningful CPU improvement found during the
            # Phase 4 adversarial review's efficiency pass (see CHANGELOG.md).
            start_idx = bisect.bisect_left(symbol_dates[symbol], cutoff, hi=end_idx)
            history[symbol] = bars[start_idx:end_idx]
        return history

    def _queue_order(self, intent: OrderIntent, decided_date: date) -> PendingOrder:
        target_fill_date = decided_date + timedelta(days=1)
        expiry_date = decided_date + timedelta(days=self._config.limit_order_expiry_days)
        return PendingOrder(
            intent=intent, decided_date=decided_date, target_fill_date=target_fill_date, expiry_date=expiry_date
        )

    @staticmethod
    def _reject_unfilled_orders_at_end(
        pending_orders: list[PendingOrder], rejected_orders: list[tuple[OrderIntent, str]]
    ) -> None:
        for pending in pending_orders:
            rejected_orders.append((pending.intent, "Backtest ended before this order could be filled or expired."))
