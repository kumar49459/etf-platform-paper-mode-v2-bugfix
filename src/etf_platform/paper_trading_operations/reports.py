"""OperationalReportGenerator (Milestone 5B, requirement 3). Daily,
weekly, monthly reports over an ExtendedPaperTradingSession's log.

DESIGN NOTE, found while building this rather than assumed correct: a
CycleLogEntry is captured once, at the moment a cycle is first processed
-- but an order logged as SUBMITTED that day may be resolved (to FILLED,
CANCELLED, or reverted to VERIFIED for retry) by a LATER day's scheduled
reconciliation. A report that trusted the frozen log entry's status
would show stale, potentially misleading state for any order still
in-flight at the moment it was logged. This generator re-queries each
execution_id's CURRENT state via ExecutionStateStore.load_execution_record()
(frozen, Milestone 1, already exists -- no new query capability added to
the frozen store) at report-generation time, so a report always reflects
the latest known truth, not a snapshot frozen at first processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from etf_platform.execution_manager import OrderLifecycleState


@dataclass
class OperationalReport:
    period_label: str
    start_date: object
    end_date: object
    executed_orders: int = 0
    rejected_orders: int = 0
    cancelled_orders: int = 0
    still_pending_orders: int = 0
    total_quantity_executed: int = 0
    total_notional_executed: float = 0.0
    symbols_traded: set = field(default_factory=set)
    reconciliation_runs_in_period: int = 0
    reconciliation_mismatches_in_period: int = 0
    anomalies: list = field(default_factory=list)
    per_symbol_breakdown: dict = field(default_factory=dict)

    def render_text(self):
        lines = [
            f"=== {self.period_label} Report ({self.start_date} to {self.end_date}) ===",
            f"Executed orders:      {self.executed_orders}",
            f"Rejected orders:      {self.rejected_orders}",
            f"Cancelled orders:     {self.cancelled_orders}",
            f"Still pending:        {self.still_pending_orders}",
            f"Total qty executed:   {self.total_quantity_executed}",
            f"Total notional:       {self.total_notional_executed:,.2f}",
            f"Symbols traded:       {sorted(self.symbols_traded)}",
            f"Reconciliation runs:  {self.reconciliation_runs_in_period}",
            f"Reconciliation mismatches: {self.reconciliation_mismatches_in_period}",
        ]
        if self.anomalies:
            lines.append("ANOMALIES DETECTED:")
            for a in self.anomalies:
                lines.append(f"  - {a}")
        else:
            lines.append("Anomalies detected: none")
        return "\n".join(lines)


def _current_status(store, execution_id, fallback_status, max_retries=3):
    """Found while testing this generator against a real, failure-
    injected session: the original version caught ANY exception
    (including an injected transient DatabaseCorruptionError from
    FailureInjectingStore) and reported it identically to a genuinely
    missing record -- conflating "the read itself failed, retry would
    likely succeed" with "this record does not exist," a much more
    serious and different situation. Module 28's own design already
    treats a transient read failure as retry-able, not catastrophic; this
    generator now does the same before concluding anything is actually
    missing."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            record = store.load_execution_record(execution_id)
        except Exception as exc:
            last_exception = exc
            continue
        if record is None:
            return fallback_status, "confirmed_missing"
        return record.order_status, None
    return fallback_status, f"read_failed_after_{max_retries}_retries: {last_exception}"


def generate_report(period_label, cycle_log, store, start_date, end_date,
                     reconciliation_runs_in_period=0, reconciliation_mismatches_in_period=0):
    report = OperationalReport(
        period_label=period_label, start_date=start_date, end_date=end_date,
        reconciliation_runs_in_period=reconciliation_runs_in_period,
        reconciliation_mismatches_in_period=reconciliation_mismatches_in_period,
    )
    relevant_entries = [e for e in cycle_log if start_date <= e.as_of_date <= end_date]

    for entry in relevant_entries:
        current_status, anomaly_kind = _current_status(store, entry.execution_id, entry.final_status)
        if anomaly_kind == "confirmed_missing":
            report.anomalies.append(
                f"CONFIRMED MISSING: {entry.execution_id} ({entry.cycle_id}) has no record at report time "
                f"after retries succeeded -- this is a genuine data-loss concern."
            )
        elif anomaly_kind is not None:
            report.anomalies.append(
                f"TRANSIENT READ FAILURE (not data loss): {entry.execution_id} ({entry.cycle_id}) could not be "
                f"read after retries -- {anomaly_kind}. Using last-known status ({entry.final_status.value}) "
                f"for this report; re-run reconciliation before treating this as a real anomaly."
            )

        report.symbols_traded.add(entry.symbol)
        symbol_bucket = report.per_symbol_breakdown.setdefault(entry.symbol, {"executed": 0, "rejected": 0, "cancelled": 0})

        if current_status in (OrderLifecycleState.FILLED, OrderLifecycleState.RECONCILED):
            if entry.executed_quantity > 0:
                report.executed_orders += 1
                report.total_quantity_executed += entry.executed_quantity
                if entry.executed_price:
                    report.total_notional_executed += entry.executed_quantity * entry.executed_price
                symbol_bucket["executed"] += 1
            else:
                report.rejected_orders += 1
                symbol_bucket["rejected"] += 1
        elif current_status == OrderLifecycleState.CANCELLED:
            report.cancelled_orders += 1
            symbol_bucket["cancelled"] += 1
        elif current_status == OrderLifecycleState.FAILED:
            report.rejected_orders += 1
            symbol_bucket["rejected"] += 1
        else:
            report.still_pending_orders += 1

    return report


def generate_daily_report(cycle_log, store, day, reconciliation_runs=0, reconciliation_mismatches=0):
    return generate_report("Daily", cycle_log, store, day, day, reconciliation_runs, reconciliation_mismatches)


def generate_weekly_report(cycle_log, store, week_start, week_end, reconciliation_runs=0, reconciliation_mismatches=0):
    return generate_report("Weekly", cycle_log, store, week_start, week_end, reconciliation_runs, reconciliation_mismatches)


def generate_monthly_report(cycle_log, store, month_start, month_end, reconciliation_runs=0, reconciliation_mismatches=0):
    return generate_report("Monthly", cycle_log, store, month_start, month_end, reconciliation_runs, reconciliation_mismatches)
