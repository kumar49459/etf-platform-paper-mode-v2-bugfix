"""Tracking difference (Milestone 5A, requirement 2 - your explicit
correction to the design's original proposal). The design proposed a
guessed 25-50bps/year haircut for pre-inception periods; you correctly
rejected that as an invented assumption. This module instead measures the
REAL historical tracking difference during whatever period both the ETF
and its underlying index actually overlap, and applies that measured
figure (not a guess) to any period requiring an index-proxy extension. If
no overlap exists at all, this module returns None and the caller MUST
disclose that plainly rather than fall back to inventing a number.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass


MIN_OVERLAP_DAYS_FOR_RELIABLE_ESTIMATE = 252
"""One trading year, minimum, before treating a measured tracking
difference as usable rather than noise. Provisional, disclosed threshold."""


@dataclass(frozen=True)
class TrackingDifferenceResult:
    symbol: str
    overlap_start: object
    overlap_end: object
    overlap_trading_days: int
    annualized_tracking_difference_pct: float
    reliable: bool
    notes: str = ""


def measure_tracking_difference(etf_bars, index_bars, min_overlap_days=MIN_OVERLAP_DAYS_FOR_RELIABLE_ESTIMATE):
    etf_by_date = {b.trade_date: b.close for b in etf_bars}
    index_by_date = {b.trade_date: b.close for b in index_bars}
    overlap_dates = sorted(set(etf_by_date) & set(index_by_date))

    if len(overlap_dates) < 2:
        return None

    first_date, last_date = overlap_dates[0], overlap_dates[-1]
    etf_return = etf_by_date[last_date] / etf_by_date[first_date] - 1.0
    index_return = index_by_date[last_date] / index_by_date[first_date] - 1.0

    years = (last_date - first_date).days / 365.25
    if years <= 0:
        return None

    etf_annualized = (1 + etf_return) ** (1 / years) - 1
    index_annualized = (1 + index_return) ** (1 / years) - 1
    tracking_difference_pct = (index_annualized - etf_annualized) * 100

    reliable = len(overlap_dates) >= min_overlap_days
    notes = (
        "" if reliable else
        f"Only {len(overlap_dates)} overlapping trading days (< {min_overlap_days} required) -- "
        "this estimate is NOT reliable and must be disclosed as such, not used as if it were."
    )

    return TrackingDifferenceResult(
        symbol=etf_bars[0].symbol if etf_bars else "unknown",
        overlap_start=first_date, overlap_end=last_date, overlap_trading_days=len(overlap_dates),
        annualized_tracking_difference_pct=tracking_difference_pct, reliable=reliable, notes=notes,
    )


def apply_tracking_difference_to_proxy(index_bars, tracking_difference_result):
    if tracking_difference_result is None:
        raise ValueError(
            "No tracking difference could be measured (no overlap between ETF and index data) -- "
            "refusing to apply an unmeasured adjustment. Use the raw index-proxy series with an explicit "
            "disclosure that no tracking-difference correction was possible, rather than calling this function."
        )
    if not tracking_difference_result.reliable:
        raise ValueError(
            f"Tracking difference for {tracking_difference_result.symbol} was measured from only "
            f"{tracking_difference_result.overlap_trading_days} days -- below the reliability threshold. "
            "Refusing to apply it silently; disclose the unreliable estimate instead of using it."
        )

    daily_drag = tracking_difference_result.annualized_tracking_difference_pct / 100 / 252
    adjusted_bars = []
    cumulative_factor = 1.0
    for i, bar in enumerate(index_bars):
        cumulative_factor *= (1 - daily_drag) if i > 0 else 1.0
        adjusted_bars.append(dataclasses.replace(bar, close=bar.close * cumulative_factor, adjusted_close=bar.close * cumulative_factor))
    return adjusted_bars
