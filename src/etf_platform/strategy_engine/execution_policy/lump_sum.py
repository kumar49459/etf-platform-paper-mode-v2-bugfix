"""One-Time Lump-Sum Investment Policy (PHASE6_Objectives.md section 3.3).

Follows the identical funding rule as Recurring Monthly (Kite balance is
the only source of truth, never assume funds before confirmed) but with no
8th-EOD reminder milestone - there's no fixed monthly deadline concept for
an ad hoc contribution you initiated yourself. Triggered explicitly (by an
external caller deciding "start a lump-sum cycle now"), not by the
calendar - this policy has no month-rollover reset logic since it isn't
tied to a month at all.
"""

from __future__ import annotations

from etf_platform.strategy_engine.execution_policy.base import ExecutionPolicy
from etf_platform.strategy_engine.models import FundingState, PendingSideEffects, StrategyEngineState
from etf_platform.common.logging_setup import get_logger

logger = get_logger("strategy_engine.execution_policy.lump_sum")


class LumpSumPolicy(ExecutionPolicy):
    def run_cycle(self, as_of_date, state, cash_ledger_port, notification_port):
        notes = []
        no_effects = PendingSideEffects()
        if state is None:
            state = StrategyEngineState(
                current_month=as_of_date.strftime("%Y-%m"), funding_state=FundingState.AWAITING_FUNDS,
                reminder_sent_this_month=False, last_check_date=None,
            )
            notes.append("Lump-sum cycle initiated; funding state set to AWAITING_FUNDS.")

        if state.discontinued:
            notes.append("Strategy Engine is DISCONTINUED; no funding check performed.")
            return state, None, tuple(notes), no_effects
        if state.paused:
            notes.append("Strategy Engine is PAUSED; no funding check performed.")
            return state, None, tuple(notes), no_effects
        if state.funding_state == FundingState.IDLE:
            notes.append("Lump-sum contribution already fully allocated.")
            return state, None, tuple(notes), no_effects

        pool = cash_ledger_port.get_available_pool(as_of_date)
        state.last_check_date = as_of_date

        if pool.new_capital <= 0:
            notes.append("Lump-sum funds not yet available; zero orders will be placed; no margin or leverage used.")
            return state, None, tuple(notes), no_effects

        state.funding_state = FundingState.EXECUTING
        notes.append(f"Lump-sum funds detected (Rs.{pool.new_capital:.2f}); resuming execution immediately.")
        return state, pool, tuple(notes), no_effects

    def mark_cycle_complete(self, state, any_orders_produced):
        if any_orders_produced:
            state.funding_state = FundingState.IDLE
            logger.info("Lump-sum cycle complete; funding workflow now IDLE.")
        else:
            state.funding_state = FundingState.AWAITING_FUNDS
            logger.info("Lump-sum cycle produced zero orders; remaining AWAITING_FUNDS.")
        return state
