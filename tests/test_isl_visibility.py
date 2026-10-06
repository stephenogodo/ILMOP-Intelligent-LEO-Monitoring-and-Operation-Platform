"""
tests/test_isl_visibility.py

Tests for Layer 3: ISL visibility model.

We use synthetic satellite positions at known geometric configurations
so we can verify exact line-of-sight outcomes analytically.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from services.scheduler.isl_visibility import (
    ISLLink,
    ISLTopology,
    has_line_of_sight,
    compute_isl_topology,
    compute_isl_topology_series,
    EARTH_RADIUS_KM,
    ISL_EARTH_RADIUS_KM,
    ISL_ATMOSPHERE_MARGIN_KM,
)

T0  = 1767225600.0   # 2026-01-01 00:00:00 UTC
ALT = 550.0          # typical LEO altitude km


# ---------------------------------------------------------------------------
# Geometric helper
# ---------------------------------------------------------------------------

def _sat_at(lat_deg: float, lon_deg: float, alt_km: float) -> np.ndarray:
    """
    Return an ECI-like position for a satellite at given lat/lon/alt.
    For these tests we treat ECEF ≈ ECI — we only care about relative
    positions and distances from origin, not Earth rotation.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    r   = EARTH_RADIUS_KM + alt_km
    return np.array([
        r * math.cos(lat) * math.cos(lon),
        r * math.cos(lat) * math.sin(lon),
        r * math.sin(lat),
    ])


# ---------------------------------------------------------------------------
# has_line_of_sight tests
# ---------------------------------------------------------------------------

class TestHasLineOfSight:

    def test_same_hemisphere_clear(self):
        """Two satellites in the same hemisphere should see each other."""
        a = _sat_at( 45.0,  0.0, ALT)
        b = _sat_at( 50.0, 10.0, ALT)
        visible, _ = has_line_of_sight(a, b)
        assert visible is True

    def test_antipodal_occluded(self):
        """
        Two satellites on exactly opposite sides of Earth cannot
        see each other — the line between them passes through the core.
        """
        a = _sat_at( 0.0,   0.0, ALT)
        b = _sat_at( 0.0, 180.0, ALT)
        visible, _ = has_line_of_sight(a, b)
        assert visible is False

    def test_north_south_pole_occluded(self):
        """Satellites over opposite poles are occluded."""
        a = _sat_at( 90.0, 0.0, ALT)
        b = _sat_at(-90.0, 0.0, ALT)
        visible, _ = has_line_of_sight(a, b)
        assert visible is False

    def test_min_distance_above_threshold_for_visible(self):
        """min_distance_km must exceed ISL_EARTH_RADIUS_KM for visible pairs."""
        a = _sat_at(45.0,  0.0, ALT)
        b = _sat_at(45.0, 10.0, ALT)
        visible, min_dist = has_line_of_sight(a, b)
        if visible:
            assert min_dist > ISL_EARTH_RADIUS_KM

    def test_min_distance_below_threshold_for_antipodal(self):
        """min_distance_km must be below ISL_EARTH_RADIUS_KM for occluded pairs."""
        a = _sat_at( 0.0,   0.0, ALT)
        b = _sat_at( 0.0, 180.0, ALT)
        _, min_dist = has_line_of_sight(a, b)
        assert min_dist < ISL_EARTH_RADIUS_KM

    def test_identical_positions_no_crash(self):
        """Identical positions must not raise — returns the position radius."""
        a = _sat_at(45.0, 0.0, ALT)
        visible, dist = has_line_of_sight(a, a.copy())
        assert dist > 0

    def test_max_range_occludes_distant_pair(self):
        """A pair within LOS but beyond max_range should be flagged occluded."""
        a = _sat_at(45.0,  0.0, ALT)
        b = _sat_at(45.0, 10.0, ALT)
        actual_range = float(np.linalg.norm(b - a))
        topology = compute_isl_topology(
            {"SAT-A": a, "SAT-B": b},
            unix_time    = T0,
            max_range_km = actual_range * 0.5,
        )
        assert not topology.is_linked("SAT-A", "SAT-B")


# ---------------------------------------------------------------------------
# ISLTopology tests
# ---------------------------------------------------------------------------

class TestISLTopology:

    def _two_visible(self) -> ISLTopology:
        a = _sat_at(45.0,  0.0, ALT)
        b = _sat_at(50.0, 10.0, ALT)
        return compute_isl_topology({"SAT-A1": a, "SAT-A2": b}, T0)

    def _two_occluded(self) -> ISLTopology:
        a = _sat_at( 0.0,   0.0, ALT)
        b = _sat_at( 0.0, 180.0, ALT)
        return compute_isl_topology({"SAT-A1": a, "SAT-A2": b}, T0)

    def test_visible_pair_in_visible_links(self):
        t = self._two_visible()
        assert len(t.visible_links)  == 1
        assert len(t.occluded_links) == 0

    def test_occluded_pair_in_occluded_links(self):
        t = self._two_occluded()
        assert len(t.occluded_links) == 1
        assert len(t.visible_links)  == 0

    def test_is_linked_true_for_visible(self):
        t = self._two_visible()
        assert t.is_linked("SAT-A1", "SAT-A2") is True

    def test_is_linked_false_for_occluded(self):
        t = self._two_occluded()
        assert t.is_linked("SAT-A1", "SAT-A2") is False

    def test_is_linked_symmetric(self):
        """is_linked(A, B) must equal is_linked(B, A)."""
        t = self._two_visible()
        assert t.is_linked("SAT-A1", "SAT-A2") == \
               t.is_linked("SAT-A2", "SAT-A1")

    def test_reachable_from_visible_satellite(self):
        t = self._two_visible()
        assert "SAT-A2" in t.reachable_from("SAT-A1")
        assert "SAT-A1" in t.reachable_from("SAT-A2")

    def test_reachable_from_occluded_satellite_empty(self):
        t = self._two_occluded()
        assert t.reachable_from("SAT-A1") == set()

    def test_total_links_is_n_choose_2(self):
        """For N satellites, there are N*(N-1)/2 unique pairs."""
        positions = {
            f"SAT-{i}": _sat_at(float(i * 10), 0.0, ALT)
            for i in range(4)
        }
        t = compute_isl_topology(positions, T0)
        assert len(t.links) == 6   # 4 choose 2 = 6

    def test_unix_time_stored(self):
        t = self._two_visible()
        assert t.unix_time == T0


# ---------------------------------------------------------------------------
# Topology time series tests
# ---------------------------------------------------------------------------

class TestISLTopologySeries:

    def test_series_length_matches_input(self):
        """Output list length must match number of input epochs."""
        a = _sat_at(45.0, 0.0, ALT)
        b = _sat_at(50.0, 5.0, ALT)
        snapshots = [(T0 + i * 60, {"SAT-A": a, "SAT-B": b}) for i in range(5)]
        series = compute_isl_topology_series(snapshots)
        assert len(series) == 5

    def test_series_timestamps_preserved(self):
        """Each topology in the series must carry its correct timestamp."""
        a = _sat_at(45.0, 0.0, ALT)
        b = _sat_at(50.0, 5.0, ALT)
        times     = [T0 + i * 60 for i in range(3)]
        snapshots = [(t, {"SAT-A": a, "SAT-B": b}) for t in times]
        series    = compute_isl_topology_series(snapshots)
        for i, topo in enumerate(series):
            assert topo.unix_time == times[i]