"""Data Quality Validator (Phase 1 §1.3): the mandatory gate between raw
ingested data and every downstream module. See validator.py for the
orchestrator and checks.py for the individual, independently-testable checks.
"""

from etf_platform.data_quality.exceptions import CriticalDataQualityError, DataQualityError
from etf_platform.data_quality.models import QualityIssue, QualityReport, Severity
from etf_platform.data_quality.validator import DataQualityValidator

__all__ = [
    "DataQualityValidator",
    "QualityReport",
    "QualityIssue",
    "Severity",
    "DataQualityError",
    "CriticalDataQualityError",
]
