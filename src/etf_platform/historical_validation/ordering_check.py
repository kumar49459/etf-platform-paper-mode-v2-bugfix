"""One additional check beyond Phase 2's frozen DataQualityValidator
(requirement 4): whether the data arrived already chronologically ordered.
The frozen validator's internal checks defensively re-sort before
checking, which is correct for THEIR purpose, but it means an upstream
pipeline bug that delivers out-of-order data would never surface as a
flagged issue - it would just be silently sorted away. This is a new,
separate, small check layered on top of the frozen validator, not a
modification to it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderingIssue:
    symbol: str
    first_out_of_order_date: object
    detail: str


def check_chronological_ordering(symbol, bars):
    issues = []
    for prev, curr in zip(bars, bars[1:]):
        if curr.trade_date <= prev.trade_date:
            issues.append(OrderingIssue(
                symbol=symbol, first_out_of_order_date=curr.trade_date,
                detail=f"Bar for {curr.trade_date} appears after bar for {prev.trade_date} in the input "
                       "sequence -- data did not arrive pre-sorted, which may indicate an upstream pipeline defect.",
            ))
            break
    return issues
