"""Reproducibility manifest (Milestone 5A) and data quality orchestration.
The manifest adds the one piece Phase 4's frozen reproducibility.py
doesn't cover: a DATA version, since nothing before this milestone needed
to version a historical dataset itself. Data quality orchestration wires
together Phase 2's frozen DataQualityValidator with this package's one new
check (ordering) and the "abort if not satisfied" behavior the frozen
validator already implements via CriticalDataQualityError -- no new abort
logic invented, the frozen mechanism is reused directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from etf_platform.backtesting.reproducibility import get_code_version
from etf_platform.data_quality.exceptions import CriticalDataQualityError
from etf_platform.data_quality.validator import DataQualityValidator
from etf_platform.historical_validation.ordering_check import check_chronological_ordering


@dataclass(frozen=True)
class DataManifest:
    symbol: str
    bar_count: int
    data_hash: str
    date_range: tuple


@dataclass(frozen=True)
class ReproducibilityManifest:
    code_commit: str
    code_dirty: bool
    data_manifests: tuple
    report_schema_version: str
    random_seed: object


def compute_data_manifest(symbol, bars):
    hasher = hashlib.sha256()
    for bar in bars:
        hasher.update(f"{bar.trade_date}|{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}".encode())
    date_range = (bars[0].trade_date, bars[-1].trade_date) if bars else (None, None)
    return DataManifest(symbol=symbol, bar_count=len(bars), data_hash=hasher.hexdigest()[:16], date_range=date_range)


def build_reproducibility_manifest(repo_path, bars_by_symbol, report_schema_version="1.0", random_seed=None):
    commit, dirty = get_code_version(repo_path)
    data_manifests = tuple(compute_data_manifest(symbol, bars) for symbol, bars in bars_by_symbol.items())
    return ReproducibilityManifest(
        code_commit=commit, code_dirty=dirty, data_manifests=data_manifests,
        report_schema_version=report_schema_version, random_seed=random_seed,
    )


class DataIntegrityAbortedError(Exception):
    """Raised when the mandatory data-quality gate fails and no force
    override was supplied -- this milestone's completion criteria
    requires this gate to actually be capable of stopping the run, not
    just log a warning."""


def validate_and_gate(symbol, bars, expected_start, expected_end, corporate_actions=(), holidays=frozenset()):
    ordering_issues = check_chronological_ordering(symbol, bars)
    if ordering_issues:
        raise DataIntegrityAbortedError(
            f"Data integrity check FAILED for {symbol}: {ordering_issues[0].detail} Aborting analysis."
        )

    validator = DataQualityValidator(holidays=holidays)
    try:
        report = validator.validate(
            snapshot_id=f"historical-validation-{symbol}", symbol=symbol, bars=bars,
            corporate_actions=list(corporate_actions), expected_start=expected_start, expected_end=expected_end,
        )
    except CriticalDataQualityError as exc:
        raise DataIntegrityAbortedError(f"Data integrity check FAILED for {symbol}: {exc} Aborting analysis.") from exc
    return report
