"""Kite status mapping (Milestone 6). The concrete mapping designed and
approved during the architecture review -- (status_string, filled_quantity,
quantity) -> OrderLifecycleState, NOT a simple status-to-status lookup,
because Kite has no dedicated PARTIALLY_FILLED status string.
"""

from __future__ import annotations

from etf_platform.execution_manager.models import OrderLifecycleState

_PENDING_REGARDLESS_OF_QUANTITY = frozenset({
    "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING", "AMO REQ RECEIVED",
    "MODIFY VALIDATION PENDING", "MODIFY PENDING", "MODIFIED", "CANCEL PENDING",
})
_OPEN_LIKE = frozenset({"OPEN", "TRIGGER PENDING"})


class UnrecognizedKiteStatusError(Exception):
    """Raised when Kite returns a status string this mapping doesn't
    know about. Kite's own docs explicitly warn 'there may be other
    values as well' -- silently defaulting an unrecognized status to
    some guessed OrderLifecycleState would be exactly the kind of unsafe
    guess DDR-001 exists to prevent. Fail loudly instead."""


def map_kite_status(kite_status, filled_quantity, quantity):
    if kite_status in _PENDING_REGARDLESS_OF_QUANTITY:
        return OrderLifecycleState.PENDING
    if kite_status in _OPEN_LIKE:
        if filled_quantity <= 0:
            return OrderLifecycleState.PENDING
        if filled_quantity < quantity:
            return OrderLifecycleState.PARTIALLY_FILLED
        return OrderLifecycleState.FILLED
    if kite_status == "COMPLETE":
        return OrderLifecycleState.FILLED
    if kite_status == "CANCELLED":
        return OrderLifecycleState.CANCELLED
    if kite_status == "REJECTED":
        return OrderLifecycleState.FAILED
    raise UnrecognizedKiteStatusError(
        f"Kite returned status {kite_status!r}, which this mapping does not recognize. "
        f"Per Kite's own documentation ('there may be other values as well'), this is expected to happen "
        f"eventually -- add a deliberate mapping decision for it here, do not guess at runtime."
    )
