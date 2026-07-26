"""Tests for historical_validation: provenance, tracking difference,
extended metrics, data quality gating, reproducibility, regimes, and the
report builder's disclosure banner.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.historical_validation.extended_metrics import (
    annual_returns,
    best_worst_calendar_year,
    drawdown_episodes,
    monthly_returns,
    standalone_volatility_pct,
)
from etf_platform.historical_validation.ordering_check import check_chronological_ordering
from etf_platform.historical_validation.provenance import DataSegment, DataSource, ProvenanceTimeline
from etf_platform.historical_validation.regimes import MANDATORY_REGIMES, regime_for_date
from etf_platform.historical_validation.report_builder import DISCLOSURE_BANNER, build_report
from etf_platform.historical_validation.reproducibility_manifest import (
    DataIntegrityAbortedError,
    build_reproducibility_manifest,
    compute_data_manifest,
    validate_and_gate,
)
from etf_platform.historical_validation.synthetic_data import generate_synthetic_bars
from etf_platform.historical_validation.tracking_difference import (
    apply_tracking_difference_to_proxy,
    measure_tracking_difference,
)


class TestProvenance(unittest.TestCase):
    def test_transition_dates_identified(self):
        timeline = ProvenanceTimeline("X", (
            DataSegment("X", DataSource.INDEX_PROXY, date(2000, 1, 1), date(2005, 12, 31)),
            DataSegment("X", DataSource.ETF_ACTUAL, date(2006, 1, 1), date(2020, 1, 1)),
        ))
        self.assertEqual(timeline.transition_dates(), (date(2006, 1, 1),))

    def test_overlapping_segments_rejected(self):
        with self.assertRaises(ValueError):
            ProvenanceTimeline("X", (
                DataSegment("X", DataSource.INDEX_PROXY, date(2000, 1, 1), date(2010, 1, 1)),
                DataSegment("X", DataSource.ETF_ACTUAL, date(2005, 1, 1), date(2020, 1, 1)),
            ))

    def test_out_of_order_segments_rejected(self):
        with self.assertRaises(ValueError):
            ProvenanceTimeline("X", (
                DataSegment("X", DataSource.ETF_ACTUAL, date(2010, 1, 1), date(2020, 1, 1)),
                DataSegment("X", DataSource.INDEX_PROXY, date(2000, 1, 1), date(2005, 1, 1)),
            ))

    def test_source_at_returns_none_outside_all_segments(self):
        timeline = ProvenanceTimeline("X", (
            DataSegment("X", DataSource.ETF_ACTUAL, date(2010, 1, 1), date(2020, 1, 1)),
        ))
        self.assertIsNone(timeline.source_at(date(2005, 1, 1)))

    def test_has_any_synthetic_detects_presence(self):
        timeline = ProvenanceTimeline("X", (
            DataSegment("X", DataSource.SYNTHETIC, date(2000, 1, 1), date(2005, 1, 1)),
            DataSegment("X", DataSource.ETF_ACTUAL, date(2006, 1, 1), date(2020, 1, 1)),
        ))
        self.assertTrue(timeline.has_any_synthetic())

    def test_has_any_synthetic_false_when_all_real(self):
        timeline = ProvenanceTimeline("X", (
            DataSegment("X", DataSource.INDEX_PROXY, date(2000, 1, 1), date(2005, 1, 1)),
            DataSegment("X", DataSource.ETF_ACTUAL, date(2006, 1, 1), date(2020, 1, 1)),
        ))
        self.assertFalse(timeline.has_any_synthetic())


class TestTrackingDifference(unittest.TestCase):
    def test_no_overlap_returns_none(self):
        etf_bars = [OHLCVBar("A", date(2020, 1, 1), 100, 101, 99, 100, 1000)]
        index_bars = [OHLCVBar("A_IDX", date(2010, 1, 1), 100, 101, 99, 100, 1000)]
        self.assertIsNone(measure_tracking_difference(etf_bars, index_bars))

    def test_measures_real_difference_not_a_guess(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(400)]
        etf_bars = [OHLCVBar("A", d, 100, 100, 100, 100 * (1.0003 ** i), 1000) for i, d in enumerate(dates)]
        index_bars = [OHLCVBar("A_IDX", d, 100, 100, 100, 100 * (1.0005 ** i), 1000) for i, d in enumerate(dates)]
        result = measure_tracking_difference(etf_bars, index_bars)
        self.assertIsNotNone(result)
        self.assertGreater(result.annualized_tracking_difference_pct, 0)
        self.assertTrue(result.reliable)

    def test_short_overlap_marked_unreliable(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(10)]
        etf_bars = [OHLCVBar("A", d, 100, 100, 100, 100, 1000) for d in dates]
        index_bars = [OHLCVBar("A_IDX", d, 100, 100, 100, 101, 1000) for d in dates]
        result = measure_tracking_difference(etf_bars, index_bars)
        self.assertIsNotNone(result)
        self.assertFalse(result.reliable)

    def test_apply_refuses_when_no_measurement(self):
        with self.assertRaises(ValueError):
            apply_tracking_difference_to_proxy([], None)

    def test_apply_refuses_when_unreliable(self):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(10)]
        etf_bars = [OHLCVBar("A", d, 100, 100, 100, 100, 1000) for d in dates]
        index_bars = [OHLCVBar("A_IDX", d, 100, 100, 100, 101, 1000) for d in dates]
        result = measure_tracking_difference(etf_bars, index_bars)
        with self.assertRaises(ValueError):
            apply_tracking_difference_to_proxy(index_bars, result)


class TestExtendedMetrics(unittest.TestCase):
    def test_annual_returns_one_row_per_year(self):
        curve = [(date(2020, 1, 1), 100), (date(2020, 12, 31), 110), (date(2021, 12, 31), 121)]
        returns = annual_returns(curve)
        self.assertEqual(len(returns), 2)
        self.assertEqual(returns[0].year, 2020)

    def test_monthly_returns_one_row_per_month(self):
        curve = [(date(2020, 1, 1), 100), (date(2020, 1, 31), 105), (date(2020, 2, 28), 110)]
        returns = monthly_returns(curve)
        self.assertEqual(len(returns), 2)

    def test_best_worst_calendar_year(self):
        curve = [(date(2020, 1, 1), 100), (date(2020, 12, 31), 90), (date(2021, 12, 31), 120)]
        best, worst = best_worst_calendar_year(curve)
        self.assertEqual(worst.year, 2020)
        self.assertEqual(best.year, 2021)

    def test_drawdown_episode_recovery_time(self):
        curve = [(date(2020, 1, 1), 100), (date(2020, 2, 1), 80), (date(2020, 3, 1), 105)]
        episodes = drawdown_episodes(curve, min_drawdown_pct=5.0)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].drawdown_pct, 20.0)
        self.assertIsNotNone(episodes[0].recovery_date)

    def test_unrecovered_drawdown_has_none_recovery(self):
        curve = [(date(2020, 1, 1), 100), (date(2020, 6, 1), 60)]
        episodes = drawdown_episodes(curve, min_drawdown_pct=5.0)
        self.assertEqual(len(episodes), 1)
        self.assertIsNone(episodes[0].recovery_date)
        self.assertIsNone(episodes[0].days_to_recover)

    def test_standalone_volatility_positive(self):
        vol = standalone_volatility_pct([0.01, -0.005, 0.008, -0.012, 0.003])
        self.assertIsNotNone(vol)
        self.assertGreater(vol, 0)


class TestOrderingCheck(unittest.TestCase):
    def test_ordered_data_no_issues(self):
        bars = [OHLCVBar("A", date(2020, 1, 1) + timedelta(days=i), 100, 100, 100, 100, 1000) for i in range(5)]
        self.assertEqual(check_chronological_ordering("A", bars), [])

    def test_out_of_order_detected(self):
        bars = [OHLCVBar("A", date(2020, 1, 3), 100, 100, 100, 100, 1000), OHLCVBar("A", date(2020, 1, 1), 100, 100, 100, 100, 1000)]
        issues = check_chronological_ordering("A", bars)
        self.assertEqual(len(issues), 1)


class TestDataQualityGate(unittest.TestCase):
    def test_valid_synthetic_data_passes(self):
        bars = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        report = validate_and_gate("A_SYNTHETIC", bars, date(2023, 1, 1), date(2023, 3, 31))
        self.assertIsNotNone(report)

    def test_negative_price_aborts(self):
        import dataclasses

        bars = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        bad_bars = list(bars)
        bad_bars[3] = dataclasses.replace(bad_bars[3], close=-5.0)
        with self.assertRaises(DataIntegrityAbortedError):
            validate_and_gate("A_SYNTHETIC", bad_bars, date(2023, 1, 1), date(2023, 3, 31))

    def test_out_of_order_data_aborts(self):
        bars = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        shuffled = [bars[1], bars[0]] + bars[2:]
        with self.assertRaises(DataIntegrityAbortedError):
            validate_and_gate("A_SYNTHETIC", shuffled, date(2023, 1, 1), date(2023, 3, 31))


class TestReproducibilityManifest(unittest.TestCase):
    def test_data_manifest_hash_is_deterministic(self):
        bars = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        m1 = compute_data_manifest("A_SYNTHETIC", bars)
        m2 = compute_data_manifest("A_SYNTHETIC", bars)
        self.assertEqual(m1.data_hash, m2.data_hash)

    def test_different_data_produces_different_hash(self):
        bars1 = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        bars2 = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=2)
        m1 = compute_data_manifest("A_SYNTHETIC", bars1)
        m2 = compute_data_manifest("A_SYNTHETIC", bars2)
        self.assertNotEqual(m1.data_hash, m2.data_hash)

    def test_manifest_includes_code_version(self):
        bars = generate_synthetic_bars("A_SYNTHETIC", date(2023, 1, 1), date(2023, 3, 31), seed=1)
        manifest = build_reproducibility_manifest(".", {"A_SYNTHETIC": bars}, random_seed=42)
        self.assertIsNotNone(manifest.code_commit)
        self.assertEqual(manifest.random_seed, 42)


class TestRegimes(unittest.TestCase):
    def test_all_six_mandatory_regimes_present(self):
        names = {r.name for r in MANDATORY_REGIMES}
        expected = {
            "Dot-com Crash", "Global Financial Crisis", "2013 Taper Tantrum",
            "COVID Crash", "2022 Bear Market", "Recent Recovery Period",
        }
        self.assertEqual(names, expected)

    def test_every_regime_flagged_as_unverified(self):
        for regime in MANDATORY_REGIMES:
            self.assertFalse(regime.dates_verified, f"{regime.name} incorrectly marked as verified.")

    def test_regime_lookup_returns_none_outside_defined_windows(self):
        self.assertIsNone(regime_for_date(date(2016, 1, 1)))

    def test_regime_lookup_finds_covid(self):
        self.assertEqual(regime_for_date(date(2020, 3, 15)).name, "COVID Crash")


class TestReportBuilderDisclosure(unittest.TestCase):
    def test_banner_present_when_synthetic_data_used(self):
        timeline = ProvenanceTimeline("A", (DataSegment("A", DataSource.SYNTHETIC, date(2020, 1, 1), date(2023, 1, 1)),))
        report = build_report("1.0", "2026-07-18", [timeline])
        self.assertTrue(report.has_synthetic_data)
        self.assertIn(DISCLOSURE_BANNER, report.render_text())

    def test_banner_absent_when_no_synthetic_data(self):
        timeline = ProvenanceTimeline("A", (DataSegment("A", DataSource.ETF_ACTUAL, date(2020, 1, 1), date(2023, 1, 1)),))
        report = build_report("1.0", "2026-07-18", [timeline])
        self.assertFalse(report.has_synthetic_data)
        self.assertNotIn(DISCLOSURE_BANNER, report.render_text())

    def test_banner_present_if_any_of_multiple_timelines_has_synthetic(self):
        real_timeline = ProvenanceTimeline("A", (DataSegment("A", DataSource.ETF_ACTUAL, date(2020, 1, 1), date(2023, 1, 1)),))
        synthetic_timeline = ProvenanceTimeline("B", (DataSegment("B", DataSource.SYNTHETIC, date(2020, 1, 1), date(2023, 1, 1)),))
        report = build_report("1.0", "2026-07-18", [real_timeline, synthetic_timeline])
        self.assertTrue(report.has_synthetic_data)

    def test_banner_is_the_first_thing_in_the_rendered_report(self):
        timeline = ProvenanceTimeline("A", (DataSegment("A", DataSource.SYNTHETIC, date(2020, 1, 1), date(2023, 1, 1)),))
        report = build_report("1.0", "2026-07-18", [timeline])
        rendered = report.render_text()
        self.assertTrue(rendered.startswith(DISCLOSURE_BANNER))


class TestVerifiedETFRecords(unittest.TestCase):
    def test_all_five_mandatory_symbols_documented(self):
        from etf_platform.historical_validation.verified_etf_records import VERIFIED_ETF_RECORDS

        symbols = {r.symbol for r in VERIFIED_ETF_RECORDS}
        self.assertEqual(symbols, {"NIFTYBEES", "JUNIORBEES", "BANKBEES", "GOLDBEES", "LIQUIDBEES"})

    def test_every_record_cites_at_least_two_sources(self):
        from etf_platform.historical_validation.verified_etf_records import VERIFIED_ETF_RECORDS

        for record in VERIFIED_ETF_RECORDS:
            self.assertGreaterEqual(
                len(record.sources_checked), 2,
                f"{record.symbol} cites fewer than 2 sources -- insufficient corroboration.",
            )

    def test_confidence_field_is_one_of_two_valid_values(self):
        from etf_platform.historical_validation.verified_etf_records import VERIFIED_ETF_RECORDS

        for record in VERIFIED_ETF_RECORDS:
            self.assertIn(record.inception_date_confidence, ("verified", "conflicting_sources"))

    def test_conflicting_sources_records_have_notes_explaining_the_conflict(self):
        from etf_platform.historical_validation.verified_etf_records import VERIFIED_ETF_RECORDS

        for record in VERIFIED_ETF_RECORDS:
            if record.inception_date_confidence == "conflicting_sources":
                self.assertTrue(record.notes, f"{record.symbol} marked conflicting but has no explanatory notes.")


if __name__ == "__main__":
    unittest.main()



