"""Ambiguous execution operational report (DDR-001, requirement 2).
Generates the detailed report an operator needs to investigate and
resolve an AMBIGUOUS execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from etf_platform.execution_manager.models import OrderLifecycleState


@dataclass
class AmbiguousExecutionReport:
    execution_id: str
    cycle_id: str
    client_reference: str
    symbol: str
    quantity_proposed: int
    quantity_final: object
    limit_price: float
    created_at: object
    last_status_check: object
    broker_order_id: object
    broker_info_available: dict
    reconciliation_evidence: list
    reason_for_ambiguity: str
    recommended_operator_actions: list

    def render_text(self):
        lines = [
            "=" * 78,
            "AMBIGUOUS EXECUTION -- OPERATOR REVIEW REQUIRED",
            "=" * 78,
            "",
            f"Execution ID:        {self.execution_id}",
            f"Cycle ID:             {self.cycle_id}",
            f"Client reference:     {self.client_reference}",
            f"Symbol:               {self.symbol}",
            f"Quantity proposed:    {self.quantity_proposed}",
            f"Quantity final:       {self.quantity_final}",
            f"Limit price:          {self.limit_price}",
            f"Created at:           {self.created_at}",
            f"Last status check:    {self.last_status_check}",
            f"Local broker_order_id: {self.broker_order_id!r} (None means the broker's response, if any, was never recorded locally)",
            "",
            "BROKER INFORMATION AVAILABLE",
        ]
        if self.broker_info_available:
            for k, v in self.broker_info_available.items():
                lines.append(f"  {k}: {v}")
        else:
            lines.append("  (none -- no broker_order_id known, and no matching open order found by client_reference)")
        lines.append("")
        lines.append("RECONCILIATION EVIDENCE (what was checked, in order)")
        for i, e in enumerate(self.reconciliation_evidence, 1):
            lines.append(f"  {i}. {e}")
        lines.append("")
        lines.append("REASON FOR AMBIGUITY")
        lines.append(f"  {self.reason_for_ambiguity}")
        lines.append("")
        lines.append("RECOMMENDED OPERATOR ACTIONS")
        for a in self.recommended_operator_actions:
            lines.append(f"  - {a}")
        lines.append("")
        lines.append("Resolve via ReconciliationService.resolve_ambiguous_execution() only, after confirming "
                      "the broker's actual state directly. Do not resubmit this order manually or automatically "
                      "without first ruling out that it already reached the broker.")
        return "\n".join(lines)


def generate_ambiguous_execution_report(record, store):
    if record.order_status != OrderLifecycleState.AMBIGUOUS:
        raise ValueError(
            f"generate_ambiguous_execution_report() called for execution_id={record.execution_id!r}, "
            f"which is at {record.order_status.value!r}, not AMBIGUOUS."
        )

    broker_info = {}
    if record.broker_order_id:
        broker_info["broker_order_id"] = record.broker_order_id

    evidence = [
        f"Record reached SUBMITTED (persisted) at or before {record.last_status_check or record.created_at}.",
        "No broker_order_id was recorded locally before the crash/interruption that triggered this review.",
        f"Reconciliation searched the broker's currently-open orders for a match by client_reference "
        f"({record.cycle_id!r}) and symbol ({record.symbol!r}) -- no match found.",
        "This does NOT confirm the order never reached the broker -- it may have reached the broker and "
        "already resolved to a terminal state (filled, cancelled, or rejected) before this check ran, "
        "since terminal orders are correctly excluded from the open-orders query by design.",
    ]

    return AmbiguousExecutionReport(
        execution_id=record.execution_id, cycle_id=record.cycle_id, client_reference=record.cycle_id,
        symbol=record.symbol, quantity_proposed=record.quantity_proposed, quantity_final=record.quantity_final,
        limit_price=record.limit_price, created_at=record.created_at, last_status_check=record.last_status_check,
        broker_order_id=record.broker_order_id, broker_info_available=broker_info,
        reconciliation_evidence=evidence,
        reason_for_ambiguity=(
            "Reconciliation could not confirm either outcome: that the order reached the broker (no match "
            "found in open orders) or that it's safe to assume it didn't (a terminal order is "
            "indistinguishable from a never-submitted one using only the open-orders view). Per DDR-001, "
            "this ambiguity is never auto-resolved by guessing -- it requires an operator to check the "
            "broker's actual records directly."
        ),
        recommended_operator_actions=[
            f"Check the broker's own order history / web / app UI for any order matching symbol={record.symbol}, "
            f"quantity={record.quantity_final or record.quantity_proposed}, "
            f"limit_price={record.limit_price}, around {record.last_status_check or record.created_at}.",
            "Check contract notes or trade confirmations for the trading day in question.",
            "If found: call resolve_ambiguous_execution() with the confirmed state and the broker's actual "
            "broker_order_id/executed_quantity/executed_price.",
            "If genuinely not found anywhere at the broker: call resolve_ambiguous_execution() with "
            "confirmed_state=VERIFIED to allow a safe, confirmed retry.",
            "In all cases, operator_notes must explain what was checked and what was found -- this becomes "
            "the permanent audit trail for this resolution.",
        ],
    )
