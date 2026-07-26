"""Universe Screening Engine (Phase 3).

Runs before scoring, not folded into it: scoring z-normalizes each metric
against the universe's own distribution, and an unscreened universe still
containing illiquid or effectively-abandoned ETFs would distort that
distribution for everyone. Screening removes those first so scoring only
ever ranks genuinely viable candidates.

Every check produces PASS, FAIL, or UNKNOWN — never silently promotes
missing data to a PASS. A threshold left unset in ScreeningThresholds means
"don't evaluate this check" (the check is simply omitted from the result),
which is different from evaluating it and getting UNKNOWN because the
underlying data is missing.
"""

from __future__ import annotations

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import price_metrics
from etf_platform.etf_optimizer.models import (
    ETFMetadata,
    ScreeningCheckResult,
    ScreeningResult,
    ScreeningStatus,
    ScreeningThresholds,
)

logger = get_logger("etf_optimizer.screening_engine")


class UniverseScreeningEngine:
    def __init__(self, thresholds: ScreeningThresholds) -> None:
        self._thresholds = thresholds

    def screen(self, symbol: str, metadata: ETFMetadata, bars: list[OHLCVBar]) -> ScreeningResult:
        checks: list[ScreeningCheckResult] = []

        checks.append(self._check_history(bars))

        if self._thresholds.min_aum_crores is not None:
            checks.append(self._check_aum(metadata))

        if self._thresholds.max_expense_ratio is not None:
            checks.append(self._check_expense_ratio(metadata))

        if self._thresholds.max_tracking_error_pct is not None:
            checks.append(self._check_tracking_error(metadata))

        if self._thresholds.min_avg_daily_turnover_inr is not None:
            checks.append(self._check_turnover(bars))

        if self._thresholds.min_avg_daily_volume_units is not None:
            checks.append(self._check_volume(bars))

        overall = self._overall_status(checks)
        result = ScreeningResult(symbol=symbol, overall_status=overall, checks=tuple(checks))
        logger.info(
            "Screened %s: overall=%s (%d checks: %d pass, %d fail, %d unknown)",
            symbol, overall.value, len(checks),
            sum(1 for c in checks if c.status == ScreeningStatus.PASS),
            sum(1 for c in checks if c.status == ScreeningStatus.FAIL),
            sum(1 for c in checks if c.status == ScreeningStatus.UNKNOWN),
        )
        return result

    @staticmethod
    def _overall_status(checks: list[ScreeningCheckResult]) -> ScreeningStatus:
        if any(c.status == ScreeningStatus.FAIL for c in checks):
            return ScreeningStatus.FAIL
        if any(c.status == ScreeningStatus.UNKNOWN for c in checks):
            return ScreeningStatus.UNKNOWN
        return ScreeningStatus.PASS

    def _check_history(self, bars: list[OHLCVBar]) -> ScreeningCheckResult:
        min_days = self._thresholds.min_trading_days_history
        n = len(bars)
        if n >= min_days:
            return ScreeningCheckResult(
                "min_trading_days_history", ScreeningStatus.PASS,
                f"{n} trading days of history >= required {min_days}.",
            )
        return ScreeningCheckResult(
            "min_trading_days_history", ScreeningStatus.FAIL,
            f"Only {n} trading days of history available, need >= {min_days}. "
            "Too little history to score reliably or run statistical validation.",
        )

    def _check_aum(self, metadata: ETFMetadata) -> ScreeningCheckResult:
        threshold = self._thresholds.min_aum_crores
        if metadata.aum_crores is None:
            return ScreeningCheckResult(
                "min_aum_crores", ScreeningStatus.UNKNOWN,
                f"AUM unknown for {metadata.symbol} (metadata_source={metadata.metadata_source}); "
                f"cannot verify against minimum {threshold} crores.",
            )
        if metadata.aum_crores >= threshold:
            return ScreeningCheckResult(
                "min_aum_crores", ScreeningStatus.PASS,
                f"AUM {metadata.aum_crores:.1f} crores >= minimum {threshold} crores.",
            )
        return ScreeningCheckResult(
            "min_aum_crores", ScreeningStatus.FAIL,
            f"AUM {metadata.aum_crores:.1f} crores < minimum {threshold} crores.",
        )

    def _check_expense_ratio(self, metadata: ETFMetadata) -> ScreeningCheckResult:
        threshold = self._thresholds.max_expense_ratio
        if metadata.expense_ratio is None:
            return ScreeningCheckResult(
                "max_expense_ratio", ScreeningStatus.UNKNOWN,
                f"Expense ratio unknown for {metadata.symbol}; cannot verify against maximum {threshold}.",
            )
        if metadata.expense_ratio <= threshold:
            return ScreeningCheckResult(
                "max_expense_ratio", ScreeningStatus.PASS,
                f"Expense ratio {metadata.expense_ratio:.4f} <= maximum {threshold}.",
            )
        return ScreeningCheckResult(
            "max_expense_ratio", ScreeningStatus.FAIL,
            f"Expense ratio {metadata.expense_ratio:.4f} > maximum {threshold}.",
        )

    def _check_tracking_error(self, metadata: ETFMetadata) -> ScreeningCheckResult:
        threshold = self._thresholds.max_tracking_error_pct
        if metadata.tracking_error_pct is None:
            return ScreeningCheckResult(
                "max_tracking_error_pct", ScreeningStatus.UNKNOWN,
                f"Tracking error unknown for {metadata.symbol}; cannot verify against maximum {threshold}%.",
            )
        if metadata.tracking_error_pct <= threshold:
            return ScreeningCheckResult(
                "max_tracking_error_pct", ScreeningStatus.PASS,
                f"Tracking error {metadata.tracking_error_pct:.2f}% <= maximum {threshold}%.",
            )
        return ScreeningCheckResult(
            "max_tracking_error_pct", ScreeningStatus.FAIL,
            f"Tracking error {metadata.tracking_error_pct:.2f}% > maximum {threshold}%.",
        )

    def _check_turnover(self, bars: list[OHLCVBar]) -> ScreeningCheckResult:
        threshold = self._thresholds.min_avg_daily_turnover_inr
        turnover = price_metrics.average_daily_turnover_inr(bars)
        if turnover is None:
            return ScreeningCheckResult(
                "min_avg_daily_turnover_inr", ScreeningStatus.UNKNOWN,
                "No price history available to compute average daily turnover.",
            )
        if turnover >= threshold:
            return ScreeningCheckResult(
                "min_avg_daily_turnover_inr", ScreeningStatus.PASS,
                f"Average daily turnover Rs.{turnover:,.0f} >= minimum Rs.{threshold:,.0f}.",
            )
        return ScreeningCheckResult(
            "min_avg_daily_turnover_inr", ScreeningStatus.FAIL,
            f"Average daily turnover Rs.{turnover:,.0f} < minimum Rs.{threshold:,.0f}.",
        )

    def _check_volume(self, bars: list[OHLCVBar]) -> ScreeningCheckResult:
        threshold = self._thresholds.min_avg_daily_volume_units
        volume = price_metrics.average_daily_volume(bars)
        if volume is None:
            return ScreeningCheckResult(
                "min_avg_daily_volume_units", ScreeningStatus.UNKNOWN,
                "No price history available to compute average daily volume.",
            )
        if volume >= threshold:
            return ScreeningCheckResult(
                "min_avg_daily_volume_units", ScreeningStatus.PASS,
                f"Average daily volume {volume:,.0f} units >= minimum {threshold:,.0f}.",
            )
        return ScreeningCheckResult(
            "min_avg_daily_volume_units", ScreeningStatus.FAIL,
            f"Average daily volume {volume:,.0f} units < minimum {threshold:,.0f}.",
        )
