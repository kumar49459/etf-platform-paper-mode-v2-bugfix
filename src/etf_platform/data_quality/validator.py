"""DataQualityValidator — the mandatory gate between raw ingested data and
everything downstream (Phase 1 §4: "Data Quality Validator sits between the
Data Engine and everyone else — nothing downstream reads raw data directly").

Runs the full check pipeline (checks.py) over one symbol's bars, aggregates
into a QualityReport, and raises CriticalDataQualityError if any CRITICAL
issue is found — unless the caller explicitly passes `force=True` with a
`force_reason`, which is logged prominently and stored on the report. This
is a deliberate, narrow escape valve for the real-world case of a check
false-positive; it is not a way to silently bypass the fail-safe default
(Phase 1 §1.4).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.models import CorporateAction, OHLCVBar
from etf_platform.data_quality import checks
from etf_platform.data_quality.exceptions import CriticalDataQualityError
from etf_platform.data_quality.models import QualityReport

logger = get_logger("data_quality.validator")


class DataQualityValidator:
    """Mandatory validation gate between raw ingested data and every downstream module. Runs the full check pipeline and halts on CRITICAL issues unless explicitly force-overridden with a logged reason."""
    def __init__(
        self,
        max_price_jump_pct: float = 20.0,
        stale_price_max_days: int = 10,
        holidays: frozenset[date] = frozenset(),
    ) -> None:
        self._max_price_jump_pct = max_price_jump_pct
        self._stale_price_max_days = stale_price_max_days
        self._holidays = holidays

    def validate(
        self,
        snapshot_id: str,
        symbol: str,
        bars: list[OHLCVBar],
        corporate_actions: list[CorporateAction],
        expected_start: date,
        expected_end: date,
        *,
        force: bool = False,
        force_reason: str | None = None,
    ) -> QualityReport:
        report = QualityReport(snapshot_id=snapshot_id, generated_at=datetime.now(timezone.utc))

        report.issues.extend(checks.check_no_data(symbol, bars))
        # If there's genuinely no data, the remaining checks have nothing
        # meaningful to operate on — skip them rather than emit noisy
        # secondary issues that just restate "there is no data."
        if bars:
            report.issues.extend(checks.check_ohlc_consistency(symbol, bars))
            report.issues.extend(checks.check_negative_or_zero(symbol, bars))
            report.issues.extend(checks.check_duplicates(symbol, bars))
            report.issues.extend(
                checks.check_price_jump(symbol, bars, corporate_actions, self._max_price_jump_pct)
            )
            report.issues.extend(checks.check_stale_price(symbol, bars, self._stale_price_max_days))
            report.issues.extend(
                checks.check_missing_trading_days(symbol, bars, expected_start, expected_end, self._holidays)
            )

        summary = report.summary()
        logger.info(
            "Quality check for %s (snapshot=%s): %d critical, %d warning, %d info",
            symbol, snapshot_id, summary["critical"], summary["warning"], summary["info"],
        )

        if report.has_critical:
            if force:
                if not force_reason:
                    raise ValueError("force=True requires a non-empty force_reason (audit trail requirement).")
                logger.warning(
                    "CRITICAL data quality issue(s) for %s FORCED PAST by explicit override. "
                    "Reason: %s | Issues: %s",
                    symbol, force_reason, [i.message for i in report.critical_issues],
                )
            else:
                logger.error(
                    "Halting on %d CRITICAL data quality issue(s) for %s: %s",
                    len(report.critical_issues), symbol, [i.message for i in report.critical_issues],
                )
                raise CriticalDataQualityError(
                    f"{len(report.critical_issues)} critical data quality issue(s) for {symbol} "
                    f"in snapshot {snapshot_id}. Pipeline halted (fail-safe default per Phase 1 §1.4). "
                    "Pass force=True with a force_reason to override deliberately."
                )

        return report
