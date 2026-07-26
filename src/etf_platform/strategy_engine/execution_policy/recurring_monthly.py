"""Recurring Monthly Investment Policy (PHASE6_Objectives.md section 3) -
the permanent Monthly Funding Policy state machine.

State machine (section 3.2):
  AWAITING_FUNDS --(funds detected)--> EXECUTING --(cycle complete)--> IDLE (until next 1st)
       |--(daily check, still absent)--> AWAITING_FUNDS  [self-loop]
       |--(date >= reminder_day AND still absent AND reminder not yet sent)
                --> send ONE Telegram reminder, then --> AWAITING_FUNDS

Core rule (section 3.1): Kite's actual available cash balance is the only
source of truth. Never assume funds before CashLedgerPort confirms them.
Never use margin or leverage. A reminder is a reminder, never a
cancellation - the pending request is never dropped or expired.
"""

from __future__ import annotations

from etf_platform.strategy_engine.execution_policy.base import ExecutionPolicy
from etf_platform.strategy_engine.models import (
    ContributionSource,
    FundingState,
    PendingSideEffects,
    StrategyEngineState,
)
from etf_platform.common.logging_setup import get_logger

logger = get_logger("strategy_engine.execution_policy.recurring_monthly")

DEFAULT_REMINDER_DAY = 8
"""The 8th EOD reminder milestone, per your explicit instruction - a
reminder, not a cancellation. Configurable, not hardcoded, but this is the
approved default."""


class RecurringMonthlyPolicy(ExecutionPolicy):
    def __init__(self, reminder_day=DEFAULT_REMINDER_DAY):
        if reminder_day < 1 or reminder_day > 28:
            raise ValueError(f"reminder_day must be between 1 and 28 (safe across all months), got {reminder_day}")
        self._reminder_day = reminder_day

    def run_cycle(self, as_of_date, state, cash_ledger_port, notification_port):
        notes = []
        pending_reminder = None
        pending_contribution = None
        current_month_key = as_of_date.strftime("%Y-%m")

        if state is None or state.current_month != current_month_key:
            paused = state.paused if state else False
            discontinued = state.discontinued if state else False
            state = StrategyEngineState(
                current_month=current_month_key, funding_state=FundingState.AWAITING_FUNDS,
                reminder_sent_this_month=False, last_check_date=None,
                paused=paused, discontinued=discontinued,
            )
            pending_contribution = (0.0, as_of_date.replace(day=1), ContributionSource.RECURRING_MONTHLY.value)
            notes.append(f"New month detected ({current_month_key}); funding state reset to AWAITING_FUNDS.")

        if state.discontinued:
            notes.append("Strategy Engine is DISCONTINUED; no funding check performed.")
            return state, None, tuple(notes), PendingSideEffects(pending_reminder, pending_contribution)
        if state.paused:
            notes.append("Strategy Engine is PAUSED; no funding check performed.")
            return state, None, tuple(notes), PendingSideEffects(pending_reminder, pending_contribution)

        if state.funding_state == FundingState.IDLE:
            notes.append("This month's contribution already fully allocated; funding workflow idle until next month.")
            return state, None, tuple(notes), PendingSideEffects(pending_reminder, pending_contribution)

        # get_available_pool is a READ -- safe to call before persistence,
        # unlike send()/notify_expected_contribution() which are WRITES
        # with duplication risk (see base.py's docstring).
        pool = cash_ledger_port.get_available_pool(as_of_date)
        state.last_check_date = as_of_date

        if pool.new_capital <= 0:
            if as_of_date.day >= self._reminder_day and not state.reminder_sent_this_month:
                pending_reminder = (
                    "Reminder: this month's investment funds have not yet been detected in your Kite "
                    f"account (as of {as_of_date}). This is a reminder, not a cancellation -- the "
                    "platform will continue checking daily and will invest automatically once funds arrive."
                )
                state.reminder_sent_this_month = True
                notes.append(f"Reminder milestone (day {self._reminder_day}) reached; funds still absent; Telegram reminder pending send.")
            notes.append("Funds not yet available; zero orders will be placed; no margin or leverage used.")
            return state, None, tuple(notes), PendingSideEffects(pending_reminder, pending_contribution)

        state.funding_state = FundingState.EXECUTING
        notes.append(f"Funds detected (Rs.{pool.new_capital:.2f}); resuming execution immediately.")
        return state, pool, tuple(notes), PendingSideEffects(pending_reminder, pending_contribution)

    def mark_cycle_complete(self, state, any_orders_produced):
        if any_orders_produced:
            state.funding_state = FundingState.IDLE
            logger.info("Recurring monthly cycle complete for %s; funding workflow now IDLE until next month.", state.current_month)
        else:
            state.funding_state = FundingState.AWAITING_FUNDS
            logger.info(
                "Recurring monthly cycle for %s produced zero orders (capital insufficient for even the "
                "highest-priority opportunity); remaining AWAITING_FUNDS, not marking complete.",
                state.current_month,
            )
        return state
