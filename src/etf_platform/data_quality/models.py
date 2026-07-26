"""Data quality issue and report models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Severity(str, Enum):
    """Data quality issue severity. Only CRITICAL halts the ingestion pipeline by default."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class QualityIssue:
    """One finding from a single data quality check, for one symbol/date."""
    symbol: str
    trade_date: date | None
    check_name: str
    severity: Severity
    message: str


@dataclass
class QualityReport:
    """Aggregated result of running the full check pipeline over one
    ingestion batch. Per Phase 1 §12.6 point 5: every issue is logged, not
    just failures — near-misses (WARNING/INFO) are retained here too, since
    patterns in near-misses are often the earliest warning of a worse
    problem developing over time.
    """

    snapshot_id: str
    generated_at: datetime
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def critical_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    @property
    def warning_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def info_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == Severity.INFO]

    @property
    def has_critical(self) -> bool:
        return len(self.critical_issues) > 0

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.issues),
            "critical": len(self.critical_issues),
            "warning": len(self.warning_issues),
            "info": len(self.info_issues),
        }

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary(),
            "issues": [
                {
                    "symbol": i.symbol,
                    "trade_date": i.trade_date.isoformat() if i.trade_date else None,
                    "check_name": i.check_name,
                    "severity": i.severity.value,
                    "message": i.message,
                }
                for i in self.issues
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
