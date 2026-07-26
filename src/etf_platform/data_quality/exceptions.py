"""Exceptions raised by the Data Quality Validator."""


class DataQualityError(Exception):
    """Base class for Data Quality Validator errors."""


class CriticalDataQualityError(DataQualityError):
    """Raised when a CRITICAL-severity issue is found and not explicitly
    overridden. Per Phase 1 §1.4 fail-safe NFR: this halts the pipeline by
    default — data is never silently passed through with a known critical
    problem. See DataQualityValidator's `force` parameter for the narrow,
    logged, human-acknowledged override path.
    """
