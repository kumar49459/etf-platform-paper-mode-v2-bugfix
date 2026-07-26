"""Timezone discipline (PHASE7_Objectives.md section 8.10, found during the
Design Readiness Review - not previously addressed anywhere in this
platform).

BINDING RULE: all internal timestamps are stored and computed in UTC.
Conversion to IST (India Standard Time, UTC+5:30 - India does not observe
daylight saving, so this offset is always exactly 5:30, never seasonal)
happens at exactly ONE boundary: market-hours/expiry decisions. No naive
(timezone-unaware) datetime is ever constructed or accepted by this
module's public functions - every entry point here validates this
structurally, not by convention.

Why this matters enough to be its own module: a naive datetime silently
mixed with an aware one, or a UTC timestamp accidentally compared against
an IST one, produces a bug that passes every test that doesn't specifically
probe for it - exactly the failure mode the Design Readiness Review
flagged as "could be severe if not followed... real until verified by
dedicated tests."
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from etf_platform.execution_manager.exceptions import NaiveDatetimeError

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
"""India does not observe daylight saving time - this offset is a fixed
constant, never seasonal, unlike timezone objects for many other countries."""

NSE_MARKET_OPEN_IST = time(9, 15)
NSE_MARKET_CLOSE_IST = time(15, 30)
"""NSE's standard equity trading session. Pre-open/post-close auction
sessions exist but are out of scope for this platform's long-term,
non-time-sensitive investing use case (Phase 1 section 0) - this module
only needs to know the ordinary continuous trading window."""


def utc_now():
    """The only sanctioned way to get "now" anywhere in this module -
    always timezone-aware UTC, never naive, never local-system-time."""
    return datetime.now(UTC)


def require_aware(dt, param_name="datetime"):
    """Structural guard: raises NaiveDatetimeError immediately if `dt` has
    no timezone info, rather than letting it silently propagate into a
    comparison or persistence call where the bug would be invisible until
    it produces a wrong real-world result."""
    if dt.tzinfo is None:
        raise NaiveDatetimeError(
            f"{param_name} is a naive datetime ({dt!r}) - every datetime in this module must be "
            "timezone-aware. Use utc_now() or explicitly attach a timezone before calling this function."
        )
    return dt


def to_utc(dt):
    """Convert any aware datetime to UTC. Raises NaiveDatetimeError on a
    naive input rather than guessing which timezone was intended."""
    require_aware(dt, "dt")
    return dt.astimezone(UTC)


def to_ist(dt):
    """The single conversion boundary this module uses for market-hours
    and expiry decisions - every other computation stays in UTC."""
    require_aware(dt, "dt")
    return dt.astimezone(IST)


def is_within_nse_trading_hours(dt):
    """Whether `dt` (any aware timezone) falls within NSE's ordinary
    continuous trading session, evaluated in IST regardless of what
    timezone `dt` was originally expressed in."""
    ist_dt = to_ist(dt)
    return NSE_MARKET_OPEN_IST <= ist_dt.time() <= NSE_MARKET_CLOSE_IST
