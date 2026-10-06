"""
tests/test_geometry.py

Tests for Layer 1: ground station geometry and AOS/LOS computation.

We use the ISS TLE (a well-known LEO object) and Svalbard ground
station as a reference case, and verify known geometric properties
rather than exact numbers that would require a truth ephemeris.
"""

import math
import numpy as np
import pytest

from services.scheduler.geometry import (
    GroundStation,
    Observation,
    _geodetic_to_ecef,
    _gmst_from_unix,
    _eci_to_ecef,
    observe,
    EARTH_RADIUS_KM,
    EARTH_ROT_RAD_S
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SVALBARD = GroundStation(
    name    = "Svalbard",
    lat_deg = 78.23,
    lon_deg = 15.40,
    alt_km  = 0.458,
)

FAIRBANKS = GroundStation(
    name    = "Fairbanks",
    lat_deg = 64.86,
    lon_deg = -147.84,
    alt_km  = 0.136,
)

# A satellite directly overhead Svalbard at 550 km altitude
# (artificial case for geometric validation)
_SVALBARD_ECEF = _geodetic_to_ecef(
    SVALBARD.lat_deg, SVALBARD.lon_deg, SVALBARD.alt_km
)
_SVALBARD_UP = _SVALBARD_ECEF / np.linalg.norm(_SVALBARD_ECEF)

# Unix timestamp: 2026-01-01 00:00:00 UTC
T0 = 1767225600.0


# ---------------------------------------------------------------------------
# ECEF conversion tests
# ---------------------------------------------------------------------------

class TestECEF:
    def test_equator_prime_meridian(self):
        """0°N 0°E at sea level should be on the X-axis."""
        r = _geodetic_to_ecef(0.0, 0.0, 0.0)
        assert abs(r[0] - 6378.137) < 0.1   # X ≈ equatorial radius
        assert abs(r[1]) < 0.1               # Y ≈ 0
        assert abs(r[2]) < 0.1               # Z ≈ 0

    def test_north_pole(self):
        """90°N should be on the Z-axis."""
        r = _geodetic_to_ecef(90.0, 0.0, 0.0)
        assert abs(r[0]) < 1.0
        assert abs(r[1]) < 1.0
        assert r[2] > 6350.0   # close to polar radius

    def test_altitude_increases_magnitude(self):
        """Adding altitude should increase the ECEF vector magnitude."""
        r0 = _geodetic_to_ecef(45.0, 45.0, 0.0)
        r1 = _geodetic_to_ecef(45.0, 45.0, 100.0)
        assert np.linalg.norm(r1) > np.linalg.norm(r0)

    def test_svalbard_reasonable_position(self):
        """Svalbard should be in the northern hemisphere, Europe sector."""
        r = _SVALBARD_ECEF
        assert r[2] > 0          # north of equator → positive Z
        assert r[0] > 0          # Europe sector → positive X
        #assert np.linalg.norm(r) > EARTH_RADIUS_KM
        # AFTER (correct — uses polar radius as the minimum bound)
        EARTH_POLAR_RADIUS_KM = 6356.752   # WGS84 polar semi-axis
        assert np.linalg.norm(r) > EARTH_POLAR_RADIUS_KM

# ---------------------------------------------------------------------------
# GMST tests
# ---------------------------------------------------------------------------

class TestGMST:
    def test_gmst_in_range(self):
        """GMST must be in [0, 2π)."""
        g = _gmst_from_gmst = _gmst_from_unix(T0)
        assert 0.0 <= g < 2 * math.pi

    def test_gmst_advances_with_time(self):
        """GMST must increase with time (Earth rotates eastward)."""
        g0 = _gmst_from_unix(T0)
        g1 = _gmst_from_unix(T0 + 3600)   # 1 hour later
        # Account for wrap-around
        delta = (g1 - g0) % (2 * math.pi)
        expected = EARTH_ROT_RAD_S * 3600  # ~0.2625 rad/hr
        assert abs(delta - expected) < 1e-4

    def test_gmst_sidereal_day(self):
        """After one sidereal day (86164 s) GMST should be back to ~same."""
        g0 = _gmst_from_unix(T0)
        g1 = _gmst_from_unix(T0 + 86164.1)
        delta = abs((g1 - g0) % (2 * math.pi))
        # Should be within 0.01 radians of a full rotation
        assert delta < 0.01 or abs(delta - 2*math.pi) < 0.01


# ---------------------------------------------------------------------------
# Observation / elevation tests
# ---------------------------------------------------------------------------

class TestObservation:
    def _sat_directly_overhead(self, station: GroundStation,
                                alt_km: float = 550.0) -> tuple:
        """
        Place a satellite directly overhead the station in ECEF,
        then back-rotate to ECI for the given timestamp.
        This gives elevation = 90° by construction.
        """
        stn_ecef = _geodetic_to_ecef(
            station.lat_deg, station.lon_deg, station.alt_km
        )
        stn_unit = stn_ecef / np.linalg.norm(stn_ecef)
        sat_ecef = stn_ecef + stn_unit * alt_km

        # Back-rotate ECEF → ECI
        gmst = _gmst_from_unix(T0)
        cos_g, sin_g = math.cos(gmst), math.sin(gmst)
        # Inverse rotation: R_z(+gmst)
        R_inv = np.array([
            [cos_g, -sin_g, 0],
            [sin_g,  cos_g, 0],
            [0,      0,     1],
        ])
        sat_eci = R_inv @ sat_ecef
        vel_eci = np.array([0.0, 7.8, 0.0])   # approximate LEO velocity
        return sat_eci, vel_eci

    def test_overhead_elevation_is_90(self):
        """Satellite directly overhead should give elevation ≈ 90°."""
        sat_eci, vel_eci = self._sat_directly_overhead(SVALBARD)
        obs = observe(sat_eci, vel_eci, T0, SVALBARD)
        assert abs(obs.elevation_deg - 90.0) < 1.0

    def test_overhead_is_above_mask(self):
        """Satellite directly overhead must be above the 5° mask."""
        sat_eci, vel_eci = self._sat_directly_overhead(SVALBARD)
        obs = observe(sat_eci, vel_eci, T0, SVALBARD)
        assert obs.above_mask is True

    def test_below_horizon_not_above_mask(self):
        """Satellite below the horizon must not be above the mask."""
        # Place satellite on the opposite side of Earth
        stn_ecef = _geodetic_to_ecef(
            SVALBARD.lat_deg, SVALBARD.lon_deg, SVALBARD.alt_km
        )
        stn_unit = stn_ecef / np.linalg.norm(stn_ecef)
        # Antipodal position, 550 km altitude
        sat_ecef_antipodal = -stn_ecef + (-stn_unit) * 550.0
        gmst = _gmst_from_unix(T0)
        cos_g, sin_g = math.cos(gmst), math.sin(gmst)
        R_inv = np.array([
            [cos_g, -sin_g, 0],
            [sin_g,  cos_g, 0],
            [0,      0,     1],
        ])
        sat_eci = R_inv @ sat_ecef_antipodal
        vel_eci = np.array([0.0, 7.8, 0.0])
        obs = observe(sat_eci, vel_eci, T0, SVALBARD)
        assert obs.above_mask is False
        assert obs.elevation_deg < 0.0

    def test_range_matches_altitude_for_overhead(self):
        """Slant range for an overhead satellite ≈ altitude."""
        sat_eci, vel_eci = self._sat_directly_overhead(SVALBARD, alt_km=550.0)
        obs = observe(sat_eci, vel_eci, T0, SVALBARD)
        assert abs(obs.range_km - 550.0) < 5.0

    def test_azimuth_in_range(self):
        """Azimuth must always be in [0, 360)."""
        sat_eci, vel_eci = self._sat_directly_overhead(FAIRBANKS)
        obs = observe(sat_eci, vel_eci, T0, FAIRBANKS)
        assert 0.0 <= obs.azimuth_deg < 360.0

    def test_range_rate_sign(self):
        """
        A satellite moving directly away from the station should
        have a positive range rate (receding).
        """
        # Satellite overhead with velocity pointing straight up (away)
        stn_ecef = _geodetic_to_ecef(
            SVALBARD.lat_deg, SVALBARD.lon_deg, SVALBARD.alt_km
        )
        stn_unit = stn_ecef / np.linalg.norm(stn_ecef)
        sat_ecef = stn_ecef + stn_unit * 550.0
        gmst = _gmst_from_unix(T0)
        cos_g, sin_g = math.cos(gmst), math.sin(gmst)
        R_inv = np.array([
            [cos_g, -sin_g, 0],
            [sin_g,  cos_g, 0],
            [0,      0,     1],
        ])
        sat_eci = R_inv @ sat_ecef
        # Velocity pointing away from Earth (radially outward)
        vel_eci = R_inv @ (stn_unit * 1.0)   # 1 km/s outward
        obs = observe(sat_eci, vel_eci, T0, SVALBARD)
        assert obs.range_rate_km_s > 0.0