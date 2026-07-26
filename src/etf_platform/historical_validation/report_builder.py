"""Comprehensive report builder (Milestone 5A, requirement 8). Assembles
every section requested into one report. The one non-negotiable rule
enforced here structurally: if ANY data behind this report is SYNTHETIC
(provenance.DataSource.SYNTHETIC), the report's very first line is an
unmissable disclosure banner, and the banner is derived from the actual
provenance data passed in, not a caller-supplied flag that could be set
incorrectly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DISCLOSURE_BANNER = (
    "=" * 78 + "\n"
    "WARNING: THIS REPORT USES SYNTHETIC DATA, NOT REAL HISTORICAL MARKET DATA.\n"
    "This environment has no live data access. Every figure below is generated\n"
    "from a statistical model chosen only to be directionally plausible for\n"
    "each named regime -- NOT calibrated against, and NOT a substitute for,\n"
    "real historical ETF or index performance. Do not use any number in this\n"
    "report to make an investment decision. This report validates that the\n"
    "FRAMEWORK runs correctly end-to-end -- it does not validate the STRATEGY\n"
    "against real market history, which requires real data this environment\n"
    "does not have access to.\n"
    + "=" * 78
)


@dataclass
class HistoricalValidationReport:
    report_version: str
    generated_at: object
    has_synthetic_data: bool
    executive_summary: str = ""
    data_source_documentation: dict = field(default_factory=dict)
    data_quality_results: dict = field(default_factory=dict)
    regime_analysis: dict = field(default_factory=dict)
    performance_sections: dict = field(default_factory=dict)
    monte_carlo_robustness: object = None
    walk_forward: object = None
    observed_weaknesses: list = field(default_factory=list)
    improvement_recommendations: list = field(default_factory=list)
    reproducibility_manifest: dict = field(default_factory=dict)

    def render_text(self):
        lines = []
        if self.has_synthetic_data:
            lines.append(DISCLOSURE_BANNER)
            lines.append("")
        lines.append(f"HISTORICAL VALIDATION REPORT (schema v{self.report_version})")
        lines.append(f"Generated: {self.generated_at}")
        lines.append("")
        lines.append("EXECUTIVE SUMMARY")
        lines.append(self.executive_summary or "(not provided)")
        lines.append("")
        lines.append("DATA SOURCE DOCUMENTATION")
        for k, v in self.data_source_documentation.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("DATA QUALITY RESULTS")
        for k, v in self.data_quality_results.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
        lines.append("MARKET REGIME ANALYSIS")
        for regime_name, section in self.regime_analysis.items():
            lines.append(f"  {regime_name}: {section}")
        lines.append("")
        lines.append("PORTFOLIO PERFORMANCE")
        for segment_name, section in self.performance_sections.items():
            lines.append(f"  [{segment_name}]")
            for k, v in section.items():
                lines.append(f"    {k}: {v}")
        lines.append("")
        if self.monte_carlo_robustness is not None:
            lines.append("MONTE CARLO ROBUSTNESS")
            lines.append(f"  Simulations: {self.monte_carlo_robustness.n_simulations}")
            lines.append(f"  CAGR p5/p50/p95: {self.monte_carlo_robustness.percentile('cagr_pct', 5):.2f}% / "
                          f"{self.monte_carlo_robustness.percentile('cagr_pct', 50):.2f}% / "
                          f"{self.monte_carlo_robustness.percentile('cagr_pct', 95):.2f}%")
            lines.append(f"  Probability of loss: {self.monte_carlo_robustness.probability_of_loss():.2%}")
            lines.append("")
        if self.walk_forward is not None:
            lines.append("WALK-FORWARD VALIDATION")
            stability = self.walk_forward.stability_summary()
            lines.append(f"  Windows: {len(self.walk_forward.comparisons)}")
            lines.append(f"  Stability summary: {stability}")
            lines.append("")
        lines.append("OBSERVED WEAKNESSES")
        for w in self.observed_weaknesses:
            lines.append(f"  - {w}")
        lines.append("")
        lines.append("IMPROVEMENT RECOMMENDATIONS")
        for r in self.improvement_recommendations:
            lines.append(f"  - {r}")
        lines.append("")
        lines.append("REPRODUCIBILITY MANIFEST")
        for k, v in self.reproducibility_manifest.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


def build_report(
    report_version, generated_at, provenance_timelines, executive_summary="",
    data_source_documentation=None, data_quality_results=None, regime_analysis=None,
    performance_sections=None, monte_carlo_robustness=None, walk_forward=None,
    observed_weaknesses=None, improvement_recommendations=None, reproducibility_manifest=None,
):
    has_synthetic = any(t.has_any_synthetic() for t in provenance_timelines)
    return HistoricalValidationReport(
        report_version=report_version, generated_at=generated_at, has_synthetic_data=has_synthetic,
        executive_summary=executive_summary, data_source_documentation=data_source_documentation or {},
        data_quality_results=data_quality_results or {}, regime_analysis=regime_analysis or {},
        performance_sections=performance_sections or {}, monte_carlo_robustness=monte_carlo_robustness,
        walk_forward=walk_forward, observed_weaknesses=observed_weaknesses or [],
        improvement_recommendations=improvement_recommendations or [],
        reproducibility_manifest=reproducibility_manifest or {},
    )
