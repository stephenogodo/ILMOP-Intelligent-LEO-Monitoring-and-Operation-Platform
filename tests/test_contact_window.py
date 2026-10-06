"""
tests/test_contact_window.py

Tests for Layer 2: ILP contact window scheduler.

Uses a synthetic propagator that places satellites at known positions
so we can verify exact AOS/LOS detection, conflict identification,
and ILP scheduling outcomes.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from services.scheduler.geometry import (
    GroundStation,
    _geodetic_to_ecef,
    _gmst_from_unix,
)
from services.scheduler.contact_window import (
    SatelliteState,
    ContactWindow,
    scan_visibility,
    find_conflicts,
    solve_schedule,
    build_contact_schedule,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

T0 = 1767225600.0   # 2026-01-01 00:00:00 UTC

SVALBARD = GroundStation(
    name="Svalbard", lat_deg=78.23, lon_deg=15.40, alt_km=0.458
)
FAIRBANKS = GroundStation(
    name="Fairbanks", lat_deg=64.86, lon_deg=-147.84, alt_km=0.136
)
SINGAPORE = GroundStation(
    name="Singapore", lat_deg=1.35, lon_deg=103.82, alt_km=0.015
)


def _overhead_eci(station: GroundStation, alt_km: float,
                  unix_time: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Return ECI position and velocity for a satellite directly overhead
    a station at the given time. Elevation will be ~90°.
    """
    stn_ecef = _geodetic_to_ecef(
        station.lat_deg, station.lon_deg, station.alt_km
    )
    stn_unit = stn_ecef / np.linalg.norm(stn_ecef)
    sat_ecef = stn_ecef + stn_unit * alt_km

    gmst = _gmst_from_unix(unix_time)
    cos_g, sin_g = math.cos(gmst), math.sin(gmst)
    R_inv = np.array([
        [cos_g, -sin_g, 0],
        [sin_g,  cos_g, 0],
        [0,      0,     1],
    ])
    sat_eci = R_inv @ sat_ecef
    vel_eci = np.array([0.0, 7.8, 0.0])
    return sat_eci, vel_eci


def _below_horizon_eci(station: GroundStation,
                       unix_time: float) -> tuple[np.ndarray, np.ndarray]:
    """ECI position for a satellite below the horizon (antipodal)."""
    stn_ecef = _geodetic_to_ecef(
        station.lat_deg, station.lon_deg, station.alt_km
    )
    stn_unit = stn_ecef / np.linalg.norm(stn_ecef)
    sat_ecef = -stn_ecef + (-stn_unit) * 550.0

    gmst = _gmst_from_unix(unix_time)
    cos_g, sin_g = math.cos(gmst), math.sin(gmst)
    R_inv = np.array([
        [cos_g, -sin_g, 0],
        [sin_g,  cos_g, 0],
        [0,      0,     1],
    ])
    sat_eci = R_inv @ sat_ecef
    vel_eci = np.array([0.0, 7.8, 0.0])
    return sat_eci, vel_eci


# ---------------------------------------------------------------------------
# Synthetic propagators
# ---------------------------------------------------------------------------

def make_always_visible_propagator(station: GroundStation,
                                   sat_id: str) -> callable:
    """
    Propagator that keeps the satellite directly overhead the station
    for the entire horizon. Every time step is above the mask.
    """
    def propagator(satellite_id: str, unix_time: float) -> SatelliteState:
        pos, vel = _overhead_eci(station, 550.0, unix_time)
        return SatelliteState(
            satellite_id      = satellite_id,
            eci_position_km   = pos,
            eci_velocity_km_s = vel,
        )
    return propagator


def make_never_visible_propagator() -> callable:
    """
    Propagator that keeps the satellite below the horizon at all times.
    """
    def propagator(satellite_id: str, unix_time: float) -> SatelliteState:
        pos, vel = _below_horizon_eci(SVALBARD, unix_time)
        return SatelliteState(
            satellite_id      = satellite_id,
            eci_position_km   = pos,
            eci_velocity_km_s = vel,
        )
    return propagator


def make_timed_window_propagator(
    station: GroundStation,
    window_start: float,
    window_end: float,
) -> callable:
    """
    Propagator that is visible only within [window_start, window_end].
    Outside that interval the satellite is below the horizon.
    """
    def propagator(satellite_id: str, unix_time: float) -> SatelliteState:
        if window_start <= unix_time <= window_end:
            pos, vel = _overhead_eci(station, 550.0, unix_time)
        else:
            pos, vel = _below_horizon_eci(station, unix_time)
        return SatelliteState(
            satellite_id      = satellite_id,
            eci_position_km   = pos,
            eci_velocity_km_s = vel,
        )
    return propagator


# ---------------------------------------------------------------------------
# Visibility scan tests
# ---------------------------------------------------------------------------

class TestScanVisibility:

    def test_always_visible_produces_one_window(self):
        """A satellite always overhead should produce exactly one window."""
        prop = make_always_visible_propagator(SVALBARD, "SAT-A1")
        windows = scan_visibility(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,   # 10-minute horizon
            step_s             = 30.0,
        )
        assert len(windows) == 1

    def test_always_visible_window_covers_full_horizon(self):
        """The single window should span almost the full horizon."""
        prop = make_always_visible_propagator(SVALBARD, "SAT-A1")
        windows = scan_visibility(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert windows[0].duration_s >= 570   # at least 19 of 20 steps

    def test_never_visible_produces_no_windows(self):
        """A satellite always below the horizon produces no windows."""
        prop = make_never_visible_propagator()
        windows = scan_visibility(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert len(windows) == 0

    def test_timed_window_detected(self):
        """A satellite visible only in the middle of the horizon
        should produce exactly one window with correct timing."""
        win_start = T0 + 120
        win_end   = T0 + 360
        prop = make_timed_window_propagator(SVALBARD, win_start, win_end)
        windows = scan_visibility(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert len(windows) == 1
        # AOS should be within one step of win_start
        assert abs(windows[0].aos_unix - win_start) <= 30.0
        # LOS should be within one step of win_end
        assert abs(windows[0].los_unix - win_end) <= 30.0

    def test_two_satellites_two_windows(self):
        """Two always-visible satellites at one station → two windows."""
        prop1 = make_always_visible_propagator(SVALBARD, "SAT-A1")
        prop2 = make_always_visible_propagator(SVALBARD, "SAT-A2")

        def combined(sat_id, t):
            return prop1(sat_id, t) if sat_id == "SAT-A1" else prop2(sat_id, t)

        windows = scan_visibility(
            satellite_ids      = ["SAT-A1", "SAT-A2"],
            stations           = [SVALBARD],
            propagator         = combined,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert len(windows) == 2

    def test_window_attributes_populated(self):
        """Every ContactWindow must have valid attribute values."""
        prop = make_always_visible_propagator(SVALBARD, "SAT-A1")
        windows = scan_visibility(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        w = windows[0]
        assert w.satellite_id == "SAT-A1"
        assert w.station.name == "Svalbard"
        assert w.aos_unix < w.los_unix
        assert w.duration_s > 0
        assert w.max_elevation_deg > 5.0


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------

class TestFindConflicts:

    def _make_window(self, sat_id, station, aos, los) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = station,
            aos_unix          = aos,
            los_unix          = los,
            max_elevation_deg = 45.0,
            duration_s        = los - aos,
        )

    def test_no_conflict_sequential_windows(self):
        """Two non-overlapping windows at the same station → no conflict."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0,        T0+300)
        w2 = self._make_window("SAT-A2", SVALBARD, T0+400,    T0+700)
        conflicts = find_conflicts([w1, w2])
        assert conflicts == []

    def test_conflict_overlapping_windows(self):
        """Two overlapping windows at the same station → one conflict group."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0,      T0+500)
        w2 = self._make_window("SAT-A2", SVALBARD, T0+300,  T0+800)
        conflicts = find_conflicts([w1, w2])
        assert len(conflicts) == 1
        assert len(conflicts[0]) == 2

    def test_different_stations_no_conflict(self):
        """Same time but different stations → no conflict."""
        w1 = self._make_window("SAT-A1", SVALBARD,  T0, T0+500)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0, T0+500)
        conflicts = find_conflicts([w1, w2])
        assert conflicts == []

    def test_three_way_conflict(self):
        """Three overlapping windows at one station → one group of 3."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0,       T0+600)
        w2 = self._make_window("SAT-A2", SVALBARD, T0+200,   T0+800)
        w3 = self._make_window("SAT-A3", SVALBARD, T0+400,   T0+1000)
        conflicts = find_conflicts([w1, w2, w3])
        assert len(conflicts) == 1
        assert len(conflicts[0]) == 3


# ---------------------------------------------------------------------------
# ILP solver tests
# ---------------------------------------------------------------------------

class TestSolveSchedule:

    def _make_window(self, sat_id, station, aos, los) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = station,
            aos_unix          = aos,
            los_unix          = los,
            max_elevation_deg = 45.0,
            duration_s        = los - aos,
        )

    def test_no_conflict_all_scheduled(self):
        """With no conflicts every window should be scheduled."""
        w1 = self._make_window("SAT-A1", SVALBARD,  T0,      T0+300)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0,      T0+400)
        w3 = self._make_window("SAT-A3", SINGAPORE, T0+500,  T0+800)
        result = solve_schedule([w1, w2, w3], [])
        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 3
        assert len(result.dropped_windows)   == 0

    def test_conflict_longer_window_wins(self):
        """
        Given two conflicting windows, the longer one should be
        scheduled (ILP maximises total duration).
        """
        short = self._make_window("SAT-A1", SVALBARD, T0,     T0+200)
        long_ = self._make_window("SAT-A2", SVALBARD, T0+100, T0+500)
        conflicts = find_conflicts([short, long_])
        result    = solve_schedule([short, long_], conflicts)

        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"
        assert len(result.dropped_windows)   == 1

    def test_total_contact_time_is_sum_of_scheduled(self):
        """total_contact_s must equal the sum of scheduled durations."""
        w1 = self._make_window("SAT-A1", SVALBARD,  T0,      T0+300)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0,      T0+400)
        result = solve_schedule([w1, w2], [])
        expected = sum(w.duration_s for w in result.scheduled_windows)
        assert abs(result.total_contact_s - expected) < 0.01

    def test_empty_windows_returns_gracefully(self):
        """An empty window list must not raise — returns NoWindows status."""
        result = solve_schedule([], [])
        assert result.solver_status == "NoWindows"
        assert result.scheduled_windows == []

    def test_three_way_conflict_one_scheduled(self):
        """From a three-way conflict exactly one window is scheduled."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0,      T0+300)
        w2 = self._make_window("SAT-A2", SVALBARD, T0+100,  T0+600)
        w3 = self._make_window("SAT-A3", SVALBARD, T0+200,  T0+400)
        conflicts = find_conflicts([w1, w2, w3])
        result    = solve_schedule([w1, w2, w3], conflicts)
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"


# ---------------------------------------------------------------------------
# End-to-end pipeline test
# ---------------------------------------------------------------------------

class TestBuildContactSchedule:

    def test_full_pipeline_optimal(self):
        """
        End-to-end: two satellites always visible at different stations
        should both be scheduled with Optimal status.
        """
        prop_a1 = make_always_visible_propagator(SVALBARD,  "SAT-A1")
        prop_a2 = make_always_visible_propagator(FAIRBANKS, "SAT-A2")

        def combined(sat_id, t):
            return prop_a1(sat_id, t) if sat_id == "SAT-A1" \
                   else prop_a2(sat_id, t)

        result = build_contact_schedule(
            satellite_ids      = ["SAT-A1", "SAT-A2"],
            stations           = [SVALBARD, FAIRBANKS],
            propagator         = combined,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert result.solver_status == "Optimal"
        assert result.total_contact_s > 0
        assert len(result.scheduled_windows) >= 2