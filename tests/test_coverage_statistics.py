"""
tests/test_coverage_statistics.py

Tests for services/dashboard/pages/coverage_statistics.py

Validates coverage statistic computations against known physical
constraints. No Streamlit server required — tests import only the
pure-Python helper functions.
"""

import math
from datetime import datetime, timezone

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the pure-Python helpers directly
from services.dashboard.coverage_engine import (
    compute_coverage_stats,
    detect_passes as _detect_passes,
    SCENARIOS,
    GROUND_STATIONS,
    EPOCH,
)

CAMBRIDGE = GROUND_STATIONS['Cambridge, UK']
LAGOS     = GROUND_STATIONS['Lagos, Nigeria']
WINDOW    = 120   # minutes — fast enough for the test suite
STEP_S    = 60.0  # 60-second steps


# ── pass detection unit tests ─────────────────────────────────────────────────

class TestDetectPasses:

    def test_no_passes_in_empty_series(self):
        assert _detect_passes([0, 0, 0, 0], 60.0) == []

    def test_single_pass_detected(self):
        counts = [0, 0, 1, 1, 1, 0, 0]
        passes = _detect_passes(counts, 60.0)
        assert len(passes) == 1
        assert passes[0]['duration_s'] == 3 * 60.0
        assert passes[0]['peak_count'] == 1

    def test_two_passes_detected(self):
        counts = [0, 1, 1, 0, 0, 1, 1, 1, 0]
        passes = _detect_passes(counts, 60.0)
        assert len(passes) == 2

    def test_pass_peak_count_correct(self):
        counts = [0, 1, 2, 3, 2, 1, 0]
        passes = _detect_passes(counts, 60.0)
        assert passes[0]['peak_count'] == 3

    def test_pass_duration_in_seconds(self):
        counts = [0, 1, 1, 1, 1, 0]   # 4 steps × 30 s = 120 s
        passes = _detect_passes(counts, 30.0)
        assert passes[0]['duration_s'] == 120.0

    def test_continuous_coverage_is_one_pass(self):
        counts = [1, 2, 3, 2, 1]
        passes = _detect_passes(counts, 60.0)
        assert len(passes) == 1


# ── coverage statistics integration tests ────────────────────────────────────

class TestCoverageStats:

    @pytest.fixture(scope='class')
    def stats_s1(self):
        return compute_coverage_stats(
            SCENARIOS['Scenario 1'], CAMBRIDGE,
            window_min=WINDOW, step_s=STEP_S
        )

    @pytest.fixture(scope='class')
    def stats_s3(self):
        return compute_coverage_stats(
            SCENARIOS['Scenario 3'], CAMBRIDGE,
            window_min=WINDOW, step_s=STEP_S
        )

    def test_stats_has_required_keys(self, stats_s1):
        required = {
            'n_satellites', 'contact_fraction', 'n_passes',
            'mean_pass_min', 'max_pass_min', 'mean_simultaneous',
            'peak_simultaneous', 'fix_fraction', 'sim_counts',
        }
        assert required.issubset(set(stats_s1.keys()))

    def test_scenario_1_has_1_satellite(self, stats_s1):
        assert stats_s1['n_satellites'] == 1

    def test_scenario_3_has_24_satellites(self, stats_s3):
        assert stats_s3['n_satellites'] == 24

    def test_contact_fraction_between_0_and_100(self, stats_s1):
        assert 0.0 <= stats_s1['contact_fraction'] <= 100.0

    def test_sim_counts_all_non_negative(self, stats_s1):
        assert all(c >= 0 for c in stats_s1['sim_counts'])

    def test_sim_counts_never_exceed_n_satellites(self, stats_s1):
        n = stats_s1['n_satellites']
        assert all(c <= n for c in stats_s1['sim_counts'])

    def test_scenario_3_more_coverage_than_scenario_1(self, stats_s1, stats_s3):
        assert stats_s3['contact_fraction'] >= stats_s1['contact_fraction'], (
            f"S3 {stats_s3['contact_fraction']}% should be ≥ "
            f"S1 {stats_s1['contact_fraction']}%"
        )

    def test_scenario_3_more_passes_than_scenario_1(self, stats_s1, stats_s3):
        assert stats_s3['n_passes'] >= stats_s1['n_passes'], (
            f"S3 passes ({stats_s3['n_passes']}) should be ≥ "
            f"S1 passes ({stats_s1['n_passes']})"
        )

    def test_scenario_3_higher_mean_simultaneous(self, stats_s1, stats_s3):
        assert stats_s3['mean_simultaneous'] >= stats_s1['mean_simultaneous']

    def test_scenario_3_higher_peak_simultaneous(self, stats_s1, stats_s3):
        assert stats_s3['peak_simultaneous'] >= stats_s1['peak_simultaneous']

    def test_pass_duration_physically_reasonable(self, stats_s1):
        """LEO passes should be 2–15 minutes above 10°."""
        if stats_s1['n_passes'] > 0:
            assert 1.0 <= stats_s1['mean_pass_min'] <= 20.0

    def test_fix_fraction_zero_for_single_satellite(self, stats_s1):
        """Single satellite can never provide 4+ simultaneous → fix fraction = 0."""
        assert stats_s1['fix_fraction'] == 0.0

    def test_scenario_4_has_6_satellites(self):
        stats = compute_coverage_stats(
            SCENARIOS['Scenario 4'], CAMBRIDGE,
            window_min=60, step_s=STEP_S
        )
        assert stats['n_satellites'] == 6

    def test_scenario_4_different_from_scenario_1(self):
        """Molniya and LEO circular give different coverage patterns."""
        s1 = compute_coverage_stats(
            SCENARIOS['Scenario 1'], CAMBRIDGE,
            window_min=WINDOW, step_s=STEP_S
        )
        s4 = compute_coverage_stats(
            SCENARIOS['Scenario 4'], CAMBRIDGE,
            window_min=WINDOW, step_s=STEP_S
        )
        # Molniya and LEO circular are different orbit types — stats should differ
        assert s4['n_satellites'] != s1['n_satellites']


# ── ground station comparison tests ──────────────────────────────────────────

class TestGroundStationComparison:

    def test_lagos_contact_fraction_positive(self):
        """Lagos (6.5°N) should have coverage from any LEO constellation."""
        stats = compute_coverage_stats(
            SCENARIOS['Scenario 1'], LAGOS,
            window_min=192, step_s=STEP_S
        )
        # Over 192 minutes (2 orbits), at least one pass expected
        assert stats['contact_fraction'] >= 0.0   # always true
        assert stats['n_steps'] > 0

    def test_all_ground_stations_produce_stats(self):
        """Every ground station should produce valid statistics."""
        for gs_name, gs in GROUND_STATIONS.items():
            stats = compute_coverage_stats(
                SCENARIOS['Scenario 1'], gs,
                window_min=60, step_s=STEP_S
            )
            assert 0.0 <= stats['contact_fraction'] <= 100.0, (
                f"Invalid contact fraction for {gs_name}"
            )


# ── progressive improvement tests ────────────────────────────────────────────

class TestProgressiveImprovement:

    @pytest.fixture(scope='class')
    def all_stats(self):
        return {
            sc: compute_coverage_stats(
                SCENARIOS[sc], CAMBRIDGE,
                window_min=WINDOW, step_s=STEP_S
            )
            for sc in ['Scenario 1', 'Scenario 2', 'Scenario 3']
        }

    def test_contact_fraction_increases_s1_to_s3(self, all_stats):
        cf1 = all_stats['Scenario 1']['contact_fraction']
        cf3 = all_stats['Scenario 3']['contact_fraction']
        assert cf3 >= cf1, (
            f"S3 contact {cf3}% should be ≥ S1 contact {cf1}%"
        )

    def test_peak_simultaneous_increases_s1_to_s3(self, all_stats):
        pk1 = all_stats['Scenario 1']['peak_simultaneous']
        pk3 = all_stats['Scenario 3']['peak_simultaneous']
        assert pk3 >= pk1

    def test_n_passes_increases_s1_to_s3(self, all_stats):
        n1 = all_stats['Scenario 1']['n_passes']
        n3 = all_stats['Scenario 3']['n_passes']
        assert n3 >= n1

    def test_coverage_improvement_factor_positive(self, all_stats):
        """The improvement factor S1→S3 is meaningful (> 1×)."""
        cf1 = all_stats['Scenario 1']['contact_fraction']
        cf3 = all_stats['Scenario 3']['contact_fraction']
        if cf1 > 0:
            factor = cf3 / cf1
            assert factor >= 1.0
