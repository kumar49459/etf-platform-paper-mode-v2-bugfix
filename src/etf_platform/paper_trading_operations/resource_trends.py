"""Resource trend analysis (Milestone 5B, requirements 2/6). Turns
ExtendedPaperTradingSession's periodic ResourceSnapshot list into a trend
verdict -- "stable" vs "growing," not just a start/end comparison, which
would miss a leak that plateaus late or oscillates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrendVerdict:
    metric_name: str
    first_half_avg: float
    second_half_avg: float
    growth_ratio: float
    verdict: str


GROWTH_RATIO_STABLE_THRESHOLD = 1.25
"""Provisional, disclosed threshold: a second-half/first-half ratio below
this is treated as noise, not genuine growth. Chosen conservatively loose
because periodic purging (session.py) intentionally causes sawtooth-
shaped, not monotonic, resource usage -- a tight threshold would
misclassify normal purge-driven fluctuation as a leak."""


def analyze_trend(snapshots, metric_name):
    if len(snapshots) < 4:
        return TrendVerdict(metric_name, 0.0, 0.0, 1.0, "insufficient_data")

    values = [getattr(s, metric_name) for s in snapshots]
    mid = len(values) // 2
    first_half_avg = sum(values[:mid]) / mid
    second_half_avg = sum(values[mid:]) / (len(values) - mid)

    if first_half_avg <= 0:
        ratio = 1.0 if second_half_avg <= 0 else float("inf")
    else:
        ratio = second_half_avg / first_half_avg

    verdict = "growing" if ratio > GROWTH_RATIO_STABLE_THRESHOLD else "stable"
    return TrendVerdict(metric_name, first_half_avg, second_half_avg, ratio, verdict)


def analyze_all_trends(snapshots):
    """Found while reviewing this milestone's own long-duration run:
    cumulative_events is an intentionally monotonic running counter (it
    only ever increases, by design -- see session.py), so running it
    through the same "is this growing unexpectedly" analysis as memory/db
    size/open-order-count is a metric-selection error: a monotonic
    counter will ALWAYS be classified 'growing,' which says nothing about
    whether anything is actually leaking. Excluded from the leak-detection
    set; still reported separately as an informational total."""
    leak_detection_metrics = ("memory_kb", "db_size_bytes", "open_orders_count")
    return {m: analyze_trend(snapshots, m) for m in leak_detection_metrics}


def render_trend_report(trends):
    lines = ["=== Resource Trend Report ==="]
    for metric, trend in trends.items():
        lines.append(
            f"  {metric}: first_half_avg={trend.first_half_avg:.1f}, second_half_avg={trend.second_half_avg:.1f}, "
            f"growth_ratio={trend.growth_ratio:.3f} -> {trend.verdict.upper()}"
        )
    any_growing = any(t.verdict == "growing" for t in trends.values())
    lines.append(f"\nOVERALL: {'UNBOUNDED GROWTH DETECTED' if any_growing else 'NO UNBOUNDED GROWTH DETECTED'}")
    return "\n".join(lines)
