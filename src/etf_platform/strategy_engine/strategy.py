"""StrategyEngine (PHASE6_Objectives.md, full document).

TWO ENTRY POINTS, ONE SHARED CORE:

1. generate_orders() - Phase 4's frozen Strategy interface. Used by the
   Backtesting Engine unchanged (PHASE6_Objectives.md section 14, section
   0.1's resolution: Strategy Engine must produce OrderIntents to satisfy
   this frozen interface - there is no way around that).

2. run_daily_cycle() - the real live/paper invocation path, implementing
   the full Monthly Funding Policy state machine (section 3) via a pluggable
   ExecutionPolicy, port-based dependencies, and Pause/Resume/Discontinue
   handling.

Both funnel into _build_buy_orders(), the shared core allocation logic
(sections 5, 6, 7) - this is what makes "the same code that's validated in
backtest is what actually runs live" (Phase 1 section 4's original intent)
literally true rather than aspirational.

STRUCTURAL SELL GUARD: _build_buy_orders() asserts every constructed order
has Side.BUY before returning, unconditionally, regardless of call path.
This mirrors RiskEvent's manual-selling guard (Phase 5) - not a code
review convention, a runtime check that fires even against a hypothetical
future bug in this same file.
"""

from __future__ import annotations

from datetime import date

from etf_platform.backtesting.models import OrderIntent, OrderType, PortfolioSnapshot
from etf_platform.backtesting.strategy import Strategy
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.common.logging_setup import get_logger
from etf_platform.strategy_engine.exceptions import SellInstructionAttemptedError
from etf_platform.strategy_engine.execution_policy.base import ExecutionPolicy
from etf_platform.strategy_engine.limit_pricing import DEFAULT_LIMIT_PRICE_BUFFER_PCT, compute_limit_price
from etf_platform.strategy_engine.models import (
    Command,
    ContributionSource,
    CycleResult,
    FundingState,
    ProposedOrderExplanation,
    StrategyEngineState,
)
from etf_platform.strategy_engine.ports import (
    CashLedgerPort,
    MarketIntelligencePort,
    NotificationPort,
    NullMarketIntelligencePort,
    OperationalEventPort,
)
from etf_platform.strategy_engine.priority import prioritize_by_gap
from etf_platform.strategy_engine.state_store import StrategyStateStore

logger = get_logger("strategy_engine.strategy")


class StrategyEngine(Strategy):
    def __init__(
        self,
        target_weights,
        deployment_day_of_month=1,
        limit_price_buffer_pct=DEFAULT_LIMIT_PRICE_BUFFER_PCT,
        market_intelligence_port=None,
        cost_tax_engine=None,
    ):
        """target_weights is the last-approved allocation this instance
        will pursue - supplied by the caller (a fixed dict for a backtest
        or simple paper-trading run; refreshed periodically by whatever
        orchestrates repeated Portfolio Optimizer calls in a full
        deployment, which is a Phase 9/Scheduler concern, not Phase 6's).
        deployment_day_of_month only affects the backtesting entry point
        (generate_orders) - it approximates monthly SIP cadence within a
        backtest, where there is no real funding-wait state machine (cash
        is simply present or not in the simulated Portfolio).
        cost_tax_engine defaults to a fresh CostTaxEngine() with default
        India equity cost config -- used to size proposed quantities so
        they leave room for real transaction costs (brokerage, STT, stamp
        duty, GST), found missing during the operational adversarial
        review (see CHANGELOG.md): the naive gross-only sizing could
        propose spending up to 100% of available cash with nothing left
        for costs, systematically oversizing every proposal."""
        if not (1 <= deployment_day_of_month <= 28):
            raise ValueError(f"deployment_day_of_month must be 1-28, got {deployment_day_of_month}")
        self._target_weights = dict(target_weights)
        self._deployment_day_of_month = deployment_day_of_month
        self._limit_price_buffer_pct = limit_price_buffer_pct
        self._market_intelligence_port = market_intelligence_port or NullMarketIntelligencePort()
        self._cost_tax_engine = cost_tax_engine or CostTaxEngine()

    # ------------------------------------------------------------------
    # Entry point 1: Phase 4's frozen Strategy interface (backtesting)
    # ------------------------------------------------------------------
    def generate_orders(self, as_of_date, history, portfolio):
        if as_of_date.day != self._deployment_day_of_month:
            return []
        if portfolio.cash <= 0:
            return []

        current_weights = self._weights_from_portfolio(portfolio, history)
        return self._build_buy_orders(
            available_capital=portfolio.cash, current_weights=current_weights,
            target_weights=self._target_weights, total_portfolio_value=portfolio.total_value,
            price_history=history, as_of_date=as_of_date, funding_source=ContributionSource.RECURRING_MONTHLY,
        )

    @staticmethod
    def _weights_from_portfolio(portfolio, history):
        if portfolio.total_value <= 0:
            return {}
        weights = {}
        for symbol, qty in portfolio.positions.items():
            bars = history.get(symbol, [])
            if not bars:
                continue
            price = bars[-1].close
            if price <= 0:
                continue
            weights[symbol] = (qty * price) / portfolio.total_value
        return weights

    # ------------------------------------------------------------------
    # Entry point 2: live/paper daily invocation
    # ------------------------------------------------------------------
    def run_daily_cycle(
        self,
        as_of_date,
        current_weights,
        target_weights,
        price_history,
        execution_policy,
        cash_ledger_port,
        notification_port,
        state_store,
        operational_event_port=None,
        is_trading_day=True,
    ):
        """The real daily entry point the Scheduler (Phase 9) invokes.
        Short-lived by design (PHASE1_Architecture_SRS.md section 17): load
        state, do work, persist state, return - no loop, no sleep, no
        resident process.

        CRASH-SAFETY (added after the operational adversarial review, see
        CHANGELOG.md): state is saved in TWO checkpoints, not one.
        Checkpoint 1 happens immediately after execution_policy.run_cycle()
        returns -- before any order-building work -- which is what makes
        the reminder-sent flag crash-safe (previously it could be lost if
        the process died between sending the Telegram reminder and the
        single end-of-method save). If orders are produced, the funding
        state is deliberately left at EXECUTING, not advanced to IDLE --
        the caller must call confirm_cycle_outcome() after confirming the
        orders were actually submitted downstream. This is what prevents a
        crash-and-restart from silently marking a month "done" when nothing
        was actually invested, and what makes retrying after a crash safe:
        _build_buy_orders is a pure function, so recomputing from the same
        persisted EXECUTING state reproduces the identical proposal.
        """
        if operational_event_port:
            operational_event_port.emit("cycle_started", {"as_of_date": as_of_date.isoformat()})

        state = state_store.load()
        state_before_commands = state.funding_state if state else FundingState.AWAITING_FUNDS

        state = self._apply_pending_commands(state, as_of_date, notification_port)

        updated_state, pool, notes, pending_effects = execution_policy.run_cycle(
            as_of_date, state, cash_ledger_port, notification_port
        )

        # CHECKPOINT 1: persist immediately, BEFORE any external side
        # effect (production verification review, see CHANGELOG.md) --
        # this is what makes "state persisted before any external side
        # effect" a structural guarantee of the call order, not a
        # hopefully-narrow crash window.
        state_store.save(updated_state)

        # NOW it's safe to perform the writes the policy decided were
        # needed -- if the process dies right here, the state already on
        # disk correctly reflects what was decided, so a restart won't
        # redecide differently, even though the actual send might not have
        # gone out (an acceptable, deliberate trade-off: a missed reminder
        # is recoverable by the next day's check; a duplicated one is not
        # undoable once delivered).
        if pending_effects.expected_contribution is not None:
            amount, expected_date, source = pending_effects.expected_contribution
            cash_ledger_port.notify_expected_contribution(amount, expected_date, source)
        if pending_effects.reminder_message is not None:
            notification_port.send(pending_effects.reminder_message)

        orders = []
        cycle_id = None
        deferred = False
        notes = list(notes)

        if pool is not None:
            cycle_id = f"{updated_state.current_month}-{pool.capital_source.value}"
            if not is_trading_day:
                deferred = True
                notes.append(
                    f"Funds are available but {as_of_date} is not a trading day; order generation deferred "
                    "to the next valid trading day. Funding state remains EXECUTING so the next invocation "
                    "will retry with the same confirmed pool."
                )
            else:
                orders = self._build_buy_orders(
                    available_capital=pool.new_capital, current_weights=current_weights, target_weights=target_weights,
                    total_portfolio_value=pool.total_investable, price_history=price_history, as_of_date=as_of_date,
                    funding_source=pool.capital_source,
                )
                if not orders:
                    # Nothing was proposable (e.g. capital too small for even
                    # the top priority) -- nothing for a caller to submit or
                    # confirm, so it's safe to auto-complete this outcome
                    # right here rather than waiting on a confirm() call that
                    # will never come.
                    updated_state = execution_policy.mark_cycle_complete(updated_state, any_orders_produced=False)
                    state_store.save(updated_state)
                # else: orders WERE produced -- state deliberately stays
                # EXECUTING (as already saved at checkpoint 1) until the
                # caller calls confirm_cycle_outcome().

        result = CycleResult(
            as_of_date=as_of_date, funding_state_before=state_before_commands,
            funding_state_after=updated_state.funding_state, orders=orders,
            reminder_sent=any("reminder sent" in n.lower() for n in notes), notes=tuple(notes),
            cycle_id=cycle_id, deferred_to_next_trading_day=deferred,
        )

        if operational_event_port:
            operational_event_port.emit(
                "cycle_completed",
                {
                    "as_of_date": as_of_date.isoformat(), "num_orders": len(orders),
                    "funding_state": updated_state.funding_state.value, "deferred": deferred,
                },
            )
        logger.info(
            "Daily cycle complete for %s: %d orders (awaiting confirmation if >0), funding_state=%s -> %s, deferred=%s",
            as_of_date, len(orders), result.funding_state_before.value, result.funding_state_after.value, deferred,
        )
        return result

    def confirm_cycle_outcome(self, as_of_date, execution_policy, state_store, submitted_successfully):
        """Call this AFTER confirming (via Module 28 / the Approval Console
        pipeline, outside Phase 6's scope) whether a cycle's proposed
        orders were actually submitted successfully. This is the second
        half of the crash-safe two-phase completion described in
        run_daily_cycle's docstring -- funding state only advances to IDLE
        here, never automatically just because orders were computed.
        Idempotent: calling this twice with the same outcome is safe
        (the second call is a no-op transition since state is already IDLE
        or already AWAITING_FUNDS)."""
        state = state_store.load()
        if state is None:
            logger.warning("confirm_cycle_outcome called with no persisted state for %s; nothing to confirm.", as_of_date)
            return
        state = execution_policy.mark_cycle_complete(state, any_orders_produced=submitted_successfully)
        state_store.save(state)
        logger.info(
            "Cycle outcome confirmed for %s: submitted_successfully=%s, funding_state=%s",
            as_of_date, submitted_successfully, state.funding_state.value,
        )

    @staticmethod
    def _apply_pending_commands(state, as_of_date, notification_port):
        """Pause/Resume/Discontinue (PHASE6_Objectives.md section 10).
        Applied before the funding check so a Pause/Discontinue takes
        effect starting THIS cycle, not next. In-flight proposals from a
        PRIOR cycle are never touched here - only future cycle generation
        is affected."""
        commands = notification_port.poll_commands()
        if not commands:
            return state
        if state is None:
            state = StrategyEngineState(
                current_month=as_of_date.strftime("%Y-%m"), funding_state=FundingState.AWAITING_FUNDS,
                reminder_sent_this_month=False, last_check_date=None,
            )
        for command in commands:
            if command == Command.PAUSE:
                state.paused = True
                logger.info("PAUSE command applied as of %s.", as_of_date)
            elif command == Command.RESUME:
                state.paused = False
                logger.info("RESUME command applied as of %s.", as_of_date)
            elif command == Command.DISCONTINUE:
                state.discontinued = True
                logger.info("DISCONTINUE command applied as of %s.", as_of_date)
        return state

    # ------------------------------------------------------------------
    # Shared core: buy-only priority allocation (sections 5, 6, 7)
    # ------------------------------------------------------------------
    def _build_buy_orders(
        self, available_capital, current_weights, target_weights, total_portfolio_value,
        price_history, as_of_date, funding_source,
    ):
        if available_capital <= 0 or total_portfolio_value <= 0:
            return []

        opportunities = prioritize_by_gap(current_weights, target_weights)
        remaining_cash = available_capital
        orders = []

        for rank, opportunity in enumerate(opportunities, start=1):
            if remaining_cash <= 0:
                break
            bars = price_history.get(opportunity.symbol, [])
            if not bars:
                continue
            last_close = bars[-1].close
            if last_close <= 0:
                continue

            limit_price = compute_limit_price(last_close, self._limit_price_buffer_pct)
            rupee_gap = opportunity.gap * total_portfolio_value
            budget_for_this_symbol = min(remaining_cash, rupee_gap)
            quantity = self._affordable_quantity(limit_price, budget_for_this_symbol)
            if quantity <= 0:
                continue

            cost = self._cost_tax_engine.compute_transaction_cost(Side.BUY, limit_price, quantity)
            total_spent = quantity * limit_price + cost.total_cost

            market_context = self._describe_market_context(opportunity.symbol, as_of_date)
            explanation = ProposedOrderExplanation(
                symbol=opportunity.symbol, gap_at_decision_time=opportunity.gap, priority_rank=rank,
                funding_source=funding_source, market_context=market_context,
            )
            order = OrderIntent(
                symbol=opportunity.symbol, side=Side.BUY, order_type=OrderType.LIMIT,
                quantity=quantity, rationale=explanation.as_text(), limit_price=limit_price,
            )
            orders.append(order)
            remaining_cash -= total_spent

        self._assert_all_buy_only(orders)
        return orders

    def _affordable_quantity(self, price, budget):
        """The largest whole-unit quantity whose GROSS cost plus REAL
        transaction costs (brokerage, STT, stamp duty, GST -- via
        CostTaxEngine, not a guessed buffer) fits within budget. Found
        missing during the operational adversarial review: the original
        naive int(budget / price) could propose spending up to 100% of
        available cash on the gross purchase alone, leaving nothing for
        costs that are always due on top -- a systematic, one-directional
        oversizing of every single proposal (see CHANGELOG.md)."""
        if price <= 0 or budget <= 0:
            return 0
        quantity = int(budget / price)
        while quantity > 0:
            cost = self._cost_tax_engine.compute_transaction_cost(Side.BUY, price, quantity)
            if quantity * price + cost.total_cost <= budget:
                return quantity
            quantity -= 1
        return 0

    def _describe_market_context(self, symbol, as_of_date):
        """Read-only advisory text only (PHASE6_Objectives.md section
        21.2) - this method's return value is NEVER consulted by any
        conditional above; it only ever gets attached to explanation text
        after the buy/quantity decision is already final."""
        regime = self._market_intelligence_port.get_market_regime(as_of_date)
        if regime is None:
            return None
        return f"{regime.regime} regime, {regime.volatility_classification} volatility (as of {regime.as_of_date})"

    @staticmethod
    def _assert_all_buy_only(orders):
        for order in orders:
            if order.side != Side.BUY:
                raise SellInstructionAttemptedError(
                    f"Strategy Engine attempted to construct a non-BUY order for {order.symbol} "
                    f"(side={order.side}). This must be structurally unreachable - see exceptions.py."
                )
