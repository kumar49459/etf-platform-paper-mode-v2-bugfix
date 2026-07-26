"""Core data models for the ETF Universe Optimizer.

Design note on missing metadata: `expense_ratio` and `aum_crores` are
`float | None`, not defaulted to 0 or excluded from the dataclass. A
missing AUM is not the same fact as an AUM of zero, and treating it as such
would silently bias every downstream score. Every consumer of ETFMetadata
in this package is required to handle `None` explicitly — see
screening_engine.py's UNKNOWN status and scoring.py's handling of missing
metric inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


@dataclass(frozen=True)
class ETFMetadata:
    """Merged metadata for one ETF: provider-sourced fields (symbol, name,
    exchange, instrument_token) plus override-sourced fields (asset_class,
    index_tracked, issuer, expense_ratio, AUM) that NSE/Kite don't provide.
    See metadata_manager.py for how the merge happens and what 'source'
    means for each field.
    """

    symbol: str
    name: str
    exchange: str
    asset_class: str | None = None          # e.g. "equity_large_cap", "gold", "international_equity"
    index_tracked: str | None = None        # e.g. "NIFTY 50", "NASDAQ 100"
    issuer: str | None = None
    inception_date: date | None = None
    expense_ratio: float | None = None      # decimal, e.g. 0.0005 for 0.05%
    tracking_error_pct: float | None = None # annualized tracking error vs benchmark, in percent
    aum_crores: float | None = None         # INR crores
    aum_as_of: date | None = None
    metadata_source: str = "provider_only"  # "provider_only" | "override_only" | "merged"


class ScreeningStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # insufficient data to evaluate — never silently promoted to PASS


@dataclass(frozen=True)
class ScreeningThresholds:
    """Minimum/maximum thresholds a candidate ETF must clear to enter the
    scored universe. All optional — an unset threshold is simply not
    checked, rather than defaulting to some arbitrary number."""

    min_aum_crores: float | None = None
    max_expense_ratio: float | None = None
    max_tracking_error_pct: float | None = None
    min_avg_daily_turnover_inr: float | None = None
    min_avg_daily_volume_units: float | None = None
    min_trading_days_history: int = 60


@dataclass(frozen=True)
class ScreeningCheckResult:
    check_name: str
    status: ScreeningStatus
    detail: str


@dataclass(frozen=True)
class ScreeningResult:
    """Full screening outcome for one ETF: overall status plus every
    individual check's result, so a FAIL or UNKNOWN is always explainable,
    not just a single opaque boolean."""

    symbol: str
    overall_status: ScreeningStatus
    checks: tuple[ScreeningCheckResult, ...]

    @property
    def passed(self) -> bool:
        return self.overall_status == ScreeningStatus.PASS

    def failure_reasons(self) -> list[str]:
        return [c.detail for c in self.checks if c.status != ScreeningStatus.PASS]


@dataclass(frozen=True)
class MetricScore:
    """One metric's contribution to an ETF's composite score."""

    metric_name: str
    raw_value: float | None       # None if the metric couldn't be computed
    z_score: float | None         # None if raw_value is None, or universe has <2 comparable ETFs
    weight: float
    contribution: float           # z_score * weight, or 0.0 if z_score is None
    direction: str                # "higher_is_better" | "lower_is_better"
    note: str = ""                # e.g. "excluded: metadata unavailable"


@dataclass(frozen=True)
class ETFScore:
    """Full explainable score for one ETF: composite score plus every
    metric's individual contribution, per Phase 3's explainability
    requirement."""

    symbol: str
    composite_score: float
    metric_scores: tuple[MetricScore, ...]
    rank: int = 0  # populated by ETFUniverseOptimizer after ranking the full set


@dataclass(frozen=True)
class BootstrapTestResult:
    """Result of a block-bootstrap test comparing a candidate ETF's returns
    against an incumbent's, over their overlapping history."""

    candidate_symbol: str
    incumbent_symbol: str
    n_observations: int
    metric_name: str                 # e.g. "annualized_mean_return_diff"
    observed_diff: float
    confidence_level: float
    ci_low: float
    ci_high: float
    n_bootstrap: int
    is_significant: bool             # True iff the CI excludes zero
    favors_candidate: bool           # True iff significant AND observed_diff > 0
    candidate_max_drawdown: float
    incumbent_max_drawdown: float
    drawdown_worse: bool             # True if candidate's max drawdown exceeds incumbent's


@dataclass(frozen=True)
class ReplacementRecommendation:
    """A statistically validated suggestion to replace `incumbent_symbol`
    with `candidate_symbol`. Only ever constructed when BootstrapTestResult
    shows a statistically significant, economically meaningful improvement —
    see candidate_generator.py for the exact gate. Always carries the full
    test result so the evidence is inspectable, not just the conclusion."""

    incumbent_symbol: str
    candidate_symbol: str
    rationale: str
    test_result: BootstrapTestResult
    drawdown_tradeoff_note: str = ""


@dataclass
class UniverseOptimizationReport:
    """Top-level output of ETFUniverseOptimizer.optimize(): the full
    explainable ranking plus screening outcomes for the whole evaluated
    universe."""

    generated_at: date
    screening_results: tuple[ScreeningResult, ...]
    ranked_scores: tuple[ETFScore, ...]  # only ETFs that passed screening
    excluded_symbols: tuple[str, ...]    # FAIL or UNKNOWN screening status

    def get_score(self, symbol: str) -> ETFScore | None:
        for score in self.ranked_scores:
            if score.symbol == symbol:
                return score
        return None


@dataclass
class CandidateGenerationReport:
    """Top-level output of PortfolioCandidateGenerator.generate(): the
    current holdings' standing in the ranked universe, plus any statistically
    validated replacement recommendations — explicitly empty (not omitted)
    when no recommendation clears the evidence bar."""

    generated_at: date
    current_holdings: tuple[str, ...]
    universe_report: UniverseOptimizationReport
    replacement_recommendations: tuple[ReplacementRecommendation, ...]
    holdings_with_insufficient_data: tuple[str, ...] = field(default_factory=tuple)
