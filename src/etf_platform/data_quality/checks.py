"""Individual, independent data quality checks.

Each check is a pure function: `bars in -> issues out`. They're composed by
DataQualityValidator into a pipeline. Keeping them pure and independent
means each is trivially unit-testable in isolation with synthetic data, and
new checks can be added without touching existing ones — this matters for a
module whose entire job is to keep being trustworthy as the platform grows.
"""

from __future__ import annotations

from datetime import date, timedelta

from etf_platform.data_engine.models import CorporateAction, OHLCVBar
from etf_platform.data_quality.models import QualityIssue, Severity


def check_no_data(symbol: str, bars: list[OHLCVBar]) -> list[QualityIssue]:
    if not bars:
        return [
            QualityIssue(
                symbol=symbol,
                trade_date=None,
                check_name="no_data",
                severity=Severity.CRITICAL,
                message=f"No OHLCV data returned for {symbol} in the requested range.",
            )
        ]
    return []


def check_ohlc_consistency(symbol: str, bars: list[OHLCVBar]) -> list[QualityIssue]:
    issues = []
    for bar in bars:
        if bar.low > bar.high:
            issues.append(
                QualityIssue(
                    symbol=symbol, trade_date=bar.trade_date, check_name="ohlc_consistency",
                    severity=Severity.CRITICAL,
                    message=f"low ({bar.low}) > high ({bar.high}) on {bar.trade_date}.",
                )
            )
            continue
        if not (bar.low <= bar.open <= bar.high):
            issues.append(
                QualityIssue(
                    symbol=symbol, trade_date=bar.trade_date, check_name="ohlc_consistency",
                    severity=Severity.CRITICAL,
                    message=f"open ({bar.open}) outside [low, high] = [{bar.low}, {bar.high}] on {bar.trade_date}.",
                )
            )
        if not (bar.low <= bar.close <= bar.high):
            issues.append(
                QualityIssue(
                    symbol=symbol, trade_date=bar.trade_date, check_name="ohlc_consistency",
                    severity=Severity.CRITICAL,
                    message=f"close ({bar.close}) outside [low, high] = [{bar.low}, {bar.high}] on {bar.trade_date}.",
                )
            )
    return issues


def check_negative_or_zero(symbol: str, bars: list[OHLCVBar]) -> list[QualityIssue]:
    issues = []
    for bar in bars:
        for field_name, value in (("open", bar.open), ("high", bar.high), ("low", bar.low), ("close", bar.close)):
            if value <= 0:
                issues.append(
                    QualityIssue(
                        symbol=symbol, trade_date=bar.trade_date, check_name="negative_or_zero_price",
                        severity=Severity.CRITICAL,
                        message=f"{field_name} price is non-positive ({value}) on {bar.trade_date}.",
                    )
                )
        if bar.volume == 0:
            issues.append(
                QualityIssue(
                    symbol=symbol, trade_date=bar.trade_date, check_name="zero_volume",
                    severity=Severity.WARNING,
                    message=f"Zero traded volume on {bar.trade_date} (illiquid day or data gap).",
                )
            )
    return issues


def check_duplicates(symbol: str, bars: list[OHLCVBar]) -> list[QualityIssue]:
    seen: dict[date, int] = {}
    for bar in bars:
        seen[bar.trade_date] = seen.get(bar.trade_date, 0) + 1
    return [
        QualityIssue(
            symbol=symbol, trade_date=d, check_name="duplicate_bar", severity=Severity.CRITICAL,
            message=f"{count} rows found for {symbol} on {d}; expected exactly 1.",
        )
        for d, count in seen.items()
        if count > 1
    ]


def check_price_jump(
    symbol: str,
    bars: list[OHLCVBar],
    corporate_actions: list[CorporateAction],
    max_jump_pct: float,
) -> list[QualityIssue]:
    """Flags single-day close-to-close moves beyond `max_jump_pct` that aren't
    explained by a corporate action on that date — this is what keeps a real
    stock split from being misdiagnosed as a bad tick (see Phase 2 design
    notes / Phase 1 §12.6)."""
    issues = []
    action_dates = {ca.ex_date for ca in corporate_actions}
    sorted_bars = sorted(bars, key=lambda b: b.trade_date)
    for prev, curr in zip(sorted_bars, sorted_bars[1:]):
        if prev.close <= 0:
            continue
        pct_change = abs((curr.close - prev.close) / prev.close) * 100.0
        if pct_change > max_jump_pct:
            if curr.trade_date in action_dates:
                issues.append(
                    QualityIssue(
                        symbol=symbol, trade_date=curr.trade_date, check_name="price_jump",
                        severity=Severity.INFO,
                        message=(
                            f"{pct_change:.1f}% move on {curr.trade_date}, but explained by a "
                            "corporate action on this date."
                        ),
                    )
                )
            else:
                issues.append(
                    QualityIssue(
                        symbol=symbol, trade_date=curr.trade_date, check_name="price_jump",
                        severity=Severity.CRITICAL,
                        message=(
                            f"Unexplained {pct_change:.1f}% close-to-close move on {curr.trade_date} "
                            f"(threshold {max_jump_pct}%), with no matching corporate action."
                        ),
                    )
                )
    return issues


def check_stale_price(symbol: str, bars: list[OHLCVBar], max_stale_days: int) -> list[QualityIssue]:
    issues = []
    sorted_bars = sorted(bars, key=lambda b: b.trade_date)
    run_start_idx = 0
    for i in range(1, len(sorted_bars) + 1):
        same_as_prev = i < len(sorted_bars) and sorted_bars[i].close == sorted_bars[i - 1].close
        if not same_as_prev:
            run_length = i - run_start_idx
            if run_length > max_stale_days:
                issues.append(
                    QualityIssue(
                        symbol=symbol,
                        trade_date=sorted_bars[i - 1].trade_date,
                        check_name="stale_price",
                        severity=Severity.WARNING,
                        message=(
                            f"Close price unchanged for {run_length} consecutive trading days ending "
                            f"{sorted_bars[i - 1].trade_date} (threshold {max_stale_days})."
                        ),
                    )
                )
            run_start_idx = i
    return issues


def check_missing_trading_days(
    symbol: str,
    bars: list[OHLCVBar],
    start: date,
    end: date,
    holidays: frozenset[date] = frozenset(),
) -> list[QualityIssue]:
    """Gap detection against a simple weekday calendar minus a supplied
    holiday list. A full NSE trading-holiday calendar is out of scope for
    Phase 2 (would need its own data source and yearly maintenance) — this
    check accepts an injected holiday set so it degrades gracefully (more
    false-positive WARNINGs on holidays not yet in the set) rather than
    silently under-detecting gaps.
    """
    present_dates = {b.trade_date for b in bars}
    issues = []
    current = start
    while current <= end:
        is_weekday = current.weekday() < 5
        if is_weekday and current not in holidays and current not in present_dates:
            issues.append(
                QualityIssue(
                    symbol=symbol, trade_date=current, check_name="missing_trading_day",
                    severity=Severity.WARNING,
                    message=f"No OHLCV row for expected trading day {current}.",
                )
            )
        current += timedelta(days=1)
    return issues
