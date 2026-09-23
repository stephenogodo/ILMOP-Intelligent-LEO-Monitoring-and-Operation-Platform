"""
tests/test_orbital_profile.py

Tests for services/otfs/orbital_profile.py

Validates OrbitalFrame output against known physical constraints
for a 550 km LEO circular orbit over a mid-latitude ground station.

All tests are self-contained — no live Kafka, TimescaleDB, or
external network access required.

References
----------
- Vallado, D. A. (2013). Fundamentals of Astrodynamics and
  Applications (4th ed.). Microcosm Press.
- Montenbruck, O., & Gill, E. (2000). Satellite Orbits. Springer.
"""

import math
from datetime import datetime, timezone

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.otfs.orbital_profile import OrbitalProfileExporter, OrbitalFrame


# ── fixtures ────────────────────────────────────────────────────────────────

CAMBRIDGE_LAT = 52.205
CAMBRIDGE_LON = 0.119
CAMBRIDGE_ALT = 20.0   # metres

TEST_EPOCH = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def exporter() -> OrbitalProfileExporter:
    """Standard Cambridge ground station, 550 km LEO, 51.6° inclination."""
    return OrbitalProfileExporter(
        satellite_id    = "SAT-A1",
        ground_lat_deg  = CAMBRIDGE_LAT,
        ground_lon_deg  = CAMBRIDGE_LON,
        ground_alt_m    = CAMBRIDGE_ALT,
        altitude_km     = 550.0,
        inclination_deg = 51.6,
        raan_deg        = 45.0,
        mean_anomaly_deg= 0.0,
        min_elevation_deg = 10.0,
        epoch           = TEST_EPOCH,
    )


# ── OrbitalFrame field tests ────────────────────────────────────────────────

class TestOrbitalFrameFields:

    def test_export_returns_list_of_orbital_frames(self, exporter):
        """export() returns a list; every element is an OrbitalFrame."""
        profile = exporter.export(TEST_EPOCH, duration_s=60, step_s=1)
        assert isinstance(profile, list)
        assert len(profile) > 0
        assert all(isinstance(f, OrbitalFrame) for f in profile)

    def test_frame_count_matches_duration_and_step(self, exporter):
        """Frame count = duration_s / step_s + 1 (inclusive of both ends)."""
        profile = exporter.export(TEST_EPOCH, duration_s=60, step_s=1)
        assert len(profile) == 61   # 0 through 60 inclusive

    def test_satellite_id_propagated_to_all_frames(self, exporter):
        """Every frame carries the correct satellite_id."""
        profile = exporter.export(TEST_EPOCH, duration_s=10, step_s=1)
        assert all(f.satellite_id == "SAT-A1" for f in profile)

    def test_timestamps_are_monotonically_increasing(self, exporter):
        """Timestamps advance by step_s seconds between consecutive frames."""
        profile = exporter.export(TEST_EPOCH, duration_s=60, step_s=1)
        for i in range(1, len(profile)):
            delta = (profile[i].timestamp - profile[i-1].timestamp).total_seconds()
            assert abs(delta - 1.0) < 1e-6

    def test_timestamps_are_timezone_aware(self, exporter):
        """All timestamps carry UTC timezone info."""
        profile = exporter.export(TEST_EPOCH, duration_s=10, step_s=1)
        assert all(f.timestamp.tzinfo is not None for f in profile)


# ── range field tests ───────────────────────────────────────────────────────

class TestRangeField:

    def test_range_is_positive(self, exporter):
        """Slant range is always a positive distance."""
        profile = exporter.export(TEST_EPOCH, duration_s=60, step_s=1)
        assert all(f.range_m > 0 for f in profile)

    def test_range_minimum_is_orbital_altitude(self, exporter):
        """
        Minimum slant range ≥ satellite altitude (550 km = 550,000 m).
        At zenith the range equals the altitude exactly; at any other
        angle it is larger.
        """
        profile = exporter.export(TEST_EPOCH, duration_s=600, step_s=1)
        min_range = min(f.range_m for f in profile)
        assert min_range >= 550_000.0

    def test_range_maximum_for_leo_orbit(self, exporter):
        """
        Maximum slant range during a visible pass (elevation ≥ 10°) to a
        LEO satellite at 550 km altitude is approximately 2,500 km.
        Use a generous upper bound of 3,000 km.
        The bound applies only to in-view frames — when the satellite is
        below the horizon the geometric range can exceed 10,000 km.
        Vallado (2013), contact window geometry.
        """
        profile = exporter.export_in_view_only(
            TEST_EPOCH, duration_s=192*60, step_s=10
        )
        if not profile:
            pytest.skip("No in-view frames in this window — skip range check")
        max_range = max(f.range_m for f in profile)
        assert max_range <= 3_000_000.0

    def test_first_frame_range_rate_is_zero(self, exporter):
        """
        First frame range rate is 0.0 because there is no prior frame
        from which to compute the finite difference.
        """
        profile = exporter.export(TEST_EPOCH, duration_s=10, step_s=1)
        assert profile[0].range_rate_ms == 0.0

    def test_range_rate_magnitude_reasonable(self, exporter):
        """
        |range rate| ≤ orbital velocity (~7,600 m/s for 550 km LEO).
        Radial component is always ≤ total velocity.
        Vallado (2013).
        """
        profile = exporter.export(TEST_EPOCH, duration_s=300, step_s=1)
        for f in profile[1:]:   # skip first frame (range_rate = 0)
            assert abs(f.range_rate_ms) <= 7_700.0


# ── elevation field tests ───────────────────────────────────────────────────

class TestElevationField:

    def test_elevation_within_physical_bounds(self, exporter):
        """Elevation is always in the range −90° to +90°."""
        profile = exporter.export(TEST_EPOCH, duration_s=600, step_s=1)
        assert all(-90.0 <= f.elevation_deg <= 90.0 for f in profile)

    def test_in_view_flag_consistent_with_elevation(self, exporter):
        """in_view is True iff elevation ≥ min_elevation_deg (10°)."""
        profile = exporter.export(TEST_EPOCH, duration_s=600, step_s=1)
        for f in profile:
            expected = f.elevation_deg >= 10.0
            assert f.in_view == expected, (
                f"elevation={f.elevation_deg:.2f}° but in_view={f.in_view}"
            )

    def test_export_in_view_only_returns_subset(self, exporter):
        """export_in_view_only() is a subset of export()."""
        full    = exporter.export(TEST_EPOCH, duration_s=600, step_s=1)
        in_view = exporter.export_in_view_only(TEST_EPOCH, duration_s=600, step_s=1)
        assert len(in_view) <= len(full)
        assert all(f.in_view for f in in_view)


# ── azimuth field tests ─────────────────────────────────────────────────────

class TestAzimuthField:

    def test_azimuth_within_physical_bounds(self, exporter):
        """Azimuth is always in the range 0° to 360°."""
        profile = exporter.export(TEST_EPOCH, duration_s=600, step_s=1)
        assert all(0.0 <= f.azimuth_deg < 360.0 for f in profile)


# ── in_view and contact window tests ───────────────────────────────────────

class TestContactWindow:

    def test_at_least_one_in_view_frame_in_full_orbit(self):
        """
        Over one full orbital period (~96 minutes) the satellite should
        pass above the horizon at least once for a 51.6° inclination
        orbit observed from Cambridge (52.2°N).
        """
        exp = OrbitalProfileExporter(
            satellite_id     = "SAT-A1",
            ground_lat_deg   = CAMBRIDGE_LAT,
            ground_lon_deg   = CAMBRIDGE_LON,
            altitude_km      = 550.0,
            inclination_deg  = 51.6,
            raan_deg         = 45.0,
            mean_anomaly_deg = 0.0,
            min_elevation_deg= 10.0,
            epoch            = TEST_EPOCH,
        )
        # Export 2 full orbits (192 minutes) to guarantee a pass
        profile = exp.export(TEST_EPOCH, duration_s=192*60, step_s=10)
        in_view = [f for f in profile if f.in_view]
        assert len(in_view) > 0, (
            "No frames with elevation ≥ 10° found over 2 orbital periods"
        )

    def test_no_in_view_below_horizon_station(self):
        """
        A satellite in a 51.6° inclination orbit never reaches the
        equatorial station at 70°N — which is above the inclination.
        No in-view frames expected.

        Note: 51.6° inclination LEO covers latitudes ±51.6°.
        A station at 70°N is outside this coverage.
        """
        exp = OrbitalProfileExporter(
            satellite_id     = "SAT-A1",
            ground_lat_deg   = 70.0,   # above inclination coverage
            ground_lon_deg   = 0.0,
            altitude_km      = 550.0,
            inclination_deg  = 51.6,
            raan_deg         = 45.0,
            mean_anomaly_deg = 0.0,
            min_elevation_deg= 10.0,
            epoch            = TEST_EPOCH,
        )
        profile = exp.export(TEST_EPOCH, duration_s=96*60, step_s=10)
        in_view = [f for f in profile if f.in_view]
        assert len(in_view) == 0


# ── geodetic / coordinate helper tests ─────────────────────────────────────

class TestCoordinateHelpers:

    def test_geodetic_to_ecef_equator_prime_meridian(self):
        """
        At (lat=0, lon=0, alt=0), ECEF should be approximately
        (6,378,137, 0, 0) — Earth's equatorial radius on the X axis.
        """
        x, y, z = OrbitalProfileExporter._geodetic_to_ecef(0.0, 0.0, 0.0)
        assert abs(x - 6_378_137.0) < 10.0   # within 10 m
        assert abs(y) < 1.0
        assert abs(z) < 1.0

    def test_geodetic_to_ecef_north_pole(self):
        """
        At (lat=90, lon=0, alt=0), Z should equal Earth's polar radius.
        WGS-84 polar radius ≈ 6,356,752 m.
        """
        x, y, z = OrbitalProfileExporter._geodetic_to_ecef(90.0, 0.0, 0.0)
        assert abs(x) < 1.0
        assert abs(y) < 1.0
        assert abs(z - 6_356_752.0) < 100.0  # within 100 m
