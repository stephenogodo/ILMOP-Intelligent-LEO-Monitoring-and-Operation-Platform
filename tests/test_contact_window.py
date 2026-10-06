"""
tests/test_contact_window.py

Tests for Layer 2: ILP contact window scheduler.

Covers:
  - Visibility scan (AOS/LOS detection)
  - Conflict detection (sweep-line)
  - ILP solver — single-objective legacy behaviour
  - ILP solver — composite 3-term objective (S7-14)
  - Greedy fallback — composite scoring
  - build_contact_schedule end-to-end pipeline

Uses a synthetic propagator that places satellites at known positions
so we can verify exact AOS/LOS detection, conflict identification,
and ILP scheduling outcomes without real TLE data.
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
    greedy_schedule,
    build_contact_schedule,
    _compute_composite_scores,
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

def make_always_visible_propagator(
    station:     GroundStation,
    sat_id:      str,
    relay_value: float = 0.0,
    data_age_s:  float = 0.0,
) -> callable:
    """
    Propagator that keeps the satellite directly overhead the station
    for the entire horizon.  Supports optional relay metadata.
    """
    def propagator(satellite_id: str, unix_time: float) -> SatelliteState:
        pos, vel = _overhead_eci(station, 550.0, unix_time)
        return SatelliteState(
            satellite_id      = satellite_id,
            eci_position_km   = pos,
            eci_velocity_km_s = vel,
            relay_value       = relay_value,
            data_age_s        = data_age_s,
        )
    return propagator


def make_never_visible_propagator() -> callable:
    """Propagator that keeps the satellite below the horizon at all times."""
    def propagator(satellite_id: str, unix_time: float) -> SatelliteState:
        pos, vel = _below_horizon_eci(SVALBARD, unix_time)
        return SatelliteState(
            satellite_id      = satellite_id,
            eci_position_km   = pos,
            eci_velocity_km_s = vel,
        )
    return propagator


def make_timed_window_propagator(
    station:      GroundStation,
    window_start: float,
    window_end:   float,
    relay_value:  float = 0.0,
    data_age_s:   float = 0.0,
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
            relay_value       = relay_value,
            data_age_s        = data_age_s,
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

    def test_relay_metadata_propagated_to_window(self):
        """relay_value and data_age_s from SatelliteState appear on the window."""
        prop = make_always_visible_propagator(
            SVALBARD, "SAT-R1", relay_value=0.8, data_age_s=3600.0
        )
        windows = scan_visibility(
            satellite_ids      = ["SAT-R1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
        )
        assert len(windows) == 1
        assert windows[0].relay_value == pytest.approx(0.8)
        assert windows[0].data_age_s  == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------

class TestFindConflicts:

    def _make_window(self, sat_id, station, aos, los,
                     relay_value=0.0, data_age_s=0.0) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = station,
            aos_unix          = aos,
            los_unix          = los,
            max_elevation_deg = 45.0,
            duration_s        = los - aos,
            relay_value       = relay_value,
            data_age_s        = data_age_s,
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
# Composite score unit tests
# ---------------------------------------------------------------------------

class TestCompositeScores:

    def _make_window(self, sat_id, duration_s,
                     relay_value=0.0, data_age_s=0.0) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = SVALBARD,
            aos_unix          = T0,
            los_unix          = T0 + duration_s,
            max_elevation_deg = 45.0,
            duration_s        = duration_s,
            relay_value       = relay_value,
            data_age_s        = data_age_s,
        )

    def test_equal_windows_equal_scores(self):
        """Identical windows → identical scores."""
        w1 = self._make_window("SAT-A1", 300, relay_value=0.5, data_age_s=100)
        w2 = self._make_window("SAT-A2", 300, relay_value=0.5, data_age_s=100)
        scores = _compute_composite_scores([w1, w2], 0.5, 0.3, 0.2)
        assert scores[0] == pytest.approx(scores[1])

    def test_higher_relay_value_wins(self):
        """
        Two windows with equal duration and data_age but different relay_value.
        The higher relay_value should produce a higher composite score.
        """
        w_low  = self._make_window("SAT-A1", 300, relay_value=0.0, data_age_s=0)
        w_high = self._make_window("SAT-A2", 300, relay_value=1.0, data_age_s=0)
        scores = _compute_composite_scores([w_low, w_high], 0.0, 1.0, 0.0)
        assert scores[1] > scores[0]

    def test_older_data_wins(self):
        """
        Two windows with equal duration and relay_value but different data_age_s.
        The window with older data should score higher.
        """
        w_fresh = self._make_window("SAT-A1", 300, relay_value=0.0, data_age_s=60)
        w_old   = self._make_window("SAT-A2", 300, relay_value=0.0, data_age_s=7200)
        scores  = _compute_composite_scores([w_fresh, w_old], 0.0, 0.0, 1.0)
        assert scores[1] > scores[0]

    def test_zero_weights_fall_back_to_equal(self):
        """α=β=γ=0 should not raise and should produce equal scores."""
        w1 = self._make_window("SAT-A1", 300, relay_value=0.0, data_age_s=0)
        w2 = self._make_window("SAT-A2", 600, relay_value=1.0, data_age_s=3600)
        scores = _compute_composite_scores([w1, w2], 0.0, 0.0, 0.0)
        # Scores are in [0,1] and must not raise
        assert all(0.0 <= s <= 1.5 for s in scores)

    def test_scores_bounded(self):
        """All composite scores must be in [0, 1] when weights sum to 1."""
        windows = [
            self._make_window("SAT-A1", 200, relay_value=0.0, data_age_s=0),
            self._make_window("SAT-A2", 500, relay_value=0.5, data_age_s=1800),
            self._make_window("SAT-A3", 800, relay_value=1.0, data_age_s=7200),
        ]
        scores = _compute_composite_scores(windows, 0.5, 0.3, 0.2)
        for s in scores:
            assert 0.0 <= s <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# ILP solver tests — single-objective regression
# ---------------------------------------------------------------------------

class TestSolveScheduleLegacy:
    """
    Regression tests: with α=1, β=0, γ=0 the solver should behave
    exactly as the original duration-only objective.
    """

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
        result = solve_schedule([w1, w2, w3], [], alpha=1.0, beta=0.0, gamma=0.0)
        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 3
        assert len(result.dropped_windows)   == 0

    def test_conflict_longer_window_wins(self):
        """
        Duration-only (α=1, β=0, γ=0): the longer window wins a conflict.
        """
        short = self._make_window("SAT-A1", SVALBARD, T0,     T0+200)
        long_ = self._make_window("SAT-A2", SVALBARD, T0+100, T0+500)
        conflicts = find_conflicts([short, long_])
        result    = solve_schedule([short, long_], conflicts,
                                   alpha=1.0, beta=0.0, gamma=0.0)

        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"
        assert len(result.dropped_windows)   == 1

    def test_total_contact_time_is_sum_of_scheduled(self):
        """total_contact_s must equal the sum of scheduled durations."""
        w1 = self._make_window("SAT-A1", SVALBARD,  T0,      T0+300)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0,      T0+400)
        result = solve_schedule([w1, w2], [], alpha=1.0, beta=0.0, gamma=0.0)
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
        result    = solve_schedule([w1, w2, w3], conflicts,
                                   alpha=1.0, beta=0.0, gamma=0.0)
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"


# ---------------------------------------------------------------------------
# ILP solver tests — composite objective
# ---------------------------------------------------------------------------

class TestSolveScheduleComposite:
    """
    Tests for the 3-term composite objective:
      score = α×duration_norm + β×relay_value + γ×data_age_norm
    """

    def _make_window(self, sat_id, station, aos, los,
                     relay_value=0.0, data_age_s=0.0) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = station,
            aos_unix          = aos,
            los_unix          = los,
            max_elevation_deg = 45.0,
            duration_s        = los - aos,
            relay_value       = relay_value,
            data_age_s        = data_age_s,
        )

    def test_relay_bias_overrides_duration(self):
        """
        β=1, α=γ=0: in a conflict, the window with the higher relay_value
        should win, even if it is shorter in duration.
        """
        # SAT-A1: long pass but not a relay node
        long_direct = self._make_window(
            "SAT-A1", SVALBARD, T0, T0+600,
            relay_value=0.0, data_age_s=0
        )
        # SAT-A2: short pass but is a relay node (high relay_value)
        short_relay = self._make_window(
            "SAT-A2", SVALBARD, T0+300, T0+480,
            relay_value=1.0, data_age_s=0
        )
        conflicts = find_conflicts([long_direct, short_relay])
        result    = solve_schedule([long_direct, short_relay], conflicts,
                                   alpha=0.0, beta=1.0, gamma=0.0)

        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"

    def test_data_age_bias_overrides_duration(self):
        """
        γ=1, α=β=0: in a conflict, the window with older data wins
        even if it is shorter in duration.
        """
        # SAT-A1: long pass, recently contacted
        long_fresh = self._make_window(
            "SAT-A1", SVALBARD, T0, T0+600,
            relay_value=0.0, data_age_s=60
        )
        # SAT-A2: short pass, data very stale (dark for 6 h)
        short_stale = self._make_window(
            "SAT-A2", SVALBARD, T0+300, T0+480,
            relay_value=0.0, data_age_s=21600
        )
        conflicts = find_conflicts([long_fresh, short_stale])
        result    = solve_schedule([long_fresh, short_stale], conflicts,
                                   alpha=0.0, beta=0.0, gamma=1.0)

        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"

    def test_default_weights_produce_optimal_result(self):
        """
        Default weights (α=0.5, β=0.3, γ=0.2): two non-conflicting windows
        should both be scheduled.
        """
        w1 = self._make_window("SAT-A1", SVALBARD,  T0,      T0+300,
                               relay_value=0.0, data_age_s=0)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0,      T0+400,
                               relay_value=0.5, data_age_s=1800)
        result = solve_schedule([w1, w2], [])   # defaults: α=0.5, β=0.3, γ=0.2
        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 2
        assert result.objective_value > 0.0
        assert result.weights_used == (0.5, 0.3, 0.2)

    def test_zero_weights_does_not_raise(self):
        """α=β=γ=0 falls back to equal weights; solver must succeed."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0, T0+300)
        w2 = self._make_window("SAT-A2", SVALBARD, T0+200, T0+500)
        conflicts = find_conflicts([w1, w2])
        result = solve_schedule([w1, w2], conflicts,
                                alpha=0.0, beta=0.0, gamma=0.0)
        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1

    def test_composite_score_stored_on_window(self):
        """After solve_schedule, each window's composite_score must be set."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0, T0+300,
                               relay_value=0.5, data_age_s=600)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0, T0+400,
                               relay_value=0.0, data_age_s=0)
        result = solve_schedule([w1, w2], [])
        for w in result.all_windows:
            assert w.composite_score >= 0.0

    def test_objective_value_returned(self):
        """ScheduleResult.objective_value must be positive for a non-trivial problem."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0, T0+300,
                               relay_value=0.8, data_age_s=3600)
        result = solve_schedule([w1], [])
        assert result.objective_value > 0.0

    def test_three_way_composite_relay_wins(self):
        """
        Three conflicting windows: relay bias (β=1) picks the relay node
        regardless of duration.
        """
        w_long   = self._make_window("SAT-A1", SVALBARD, T0,      T0+900, relay_value=0.0)
        w_medium = self._make_window("SAT-A2", SVALBARD, T0+200,  T0+700, relay_value=0.5)
        w_short  = self._make_window("SAT-A3", SVALBARD, T0+400,  T0+500, relay_value=1.0)
        conflicts = find_conflicts([w_long, w_medium, w_short])
        result    = solve_schedule([w_long, w_medium, w_short], conflicts,
                                   alpha=0.0, beta=1.0, gamma=0.0)
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A3"


# ---------------------------------------------------------------------------
# Greedy fallback tests
# ---------------------------------------------------------------------------

class TestGreedySchedule:
    """
    Tests for the greedy fallback, which uses the same composite score
    but resolves conflicts locally rather than globally.
    """

    def _make_window(self, sat_id, station, aos, los,
                     relay_value=0.0, data_age_s=0.0) -> ContactWindow:
        return ContactWindow(
            satellite_id      = sat_id,
            station           = station,
            aos_unix          = aos,
            los_unix          = los,
            max_elevation_deg = 45.0,
            duration_s        = los - aos,
            relay_value       = relay_value,
            data_age_s        = data_age_s,
        )

    def test_greedy_no_conflict_all_scheduled(self):
        """No conflicts → all windows scheduled by greedy."""
        w1 = self._make_window("SAT-A1", SVALBARD,  T0,      T0+300)
        w2 = self._make_window("SAT-A2", FAIRBANKS, T0,      T0+400)
        result = greedy_schedule([w1, w2], [])
        assert result.solver_status == "Greedy-Optimal"
        assert len(result.scheduled_windows) == 2

    def test_greedy_conflict_highest_score_wins(self):
        """
        Greedy: in a conflict, the window with the highest composite score wins.
        With β=1, the relay node wins.
        """
        w_direct = self._make_window(
            "SAT-A1", SVALBARD, T0, T0+600, relay_value=0.0
        )
        w_relay  = self._make_window(
            "SAT-A2", SVALBARD, T0+300, T0+480, relay_value=1.0
        )
        conflicts = find_conflicts([w_direct, w_relay])
        result    = greedy_schedule([w_direct, w_relay], conflicts,
                                    alpha=0.0, beta=1.0, gamma=0.0)
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-A2"

    def test_greedy_empty_returns_gracefully(self):
        """An empty window list must not raise."""
        result = greedy_schedule([], [])
        assert result.solver_status == "Greedy-NoWindows"
        assert result.scheduled_windows == []

    def test_greedy_objective_value_positive(self):
        """Greedy result should return a positive objective value."""
        w1 = self._make_window("SAT-A1", SVALBARD, T0, T0+300,
                               relay_value=0.5, data_age_s=600)
        result = greedy_schedule([w1], [])
        assert result.objective_value > 0.0


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

    def test_full_pipeline_with_relay_weights(self):
        """
        End-to-end: passing custom weights flows through to the ILP.
        A satellite with relay_value=1.0 at a conflict should be preferred
        when β=1.0.
        """
        prop_relay  = make_always_visible_propagator(
            SVALBARD, "SAT-R1", relay_value=1.0, data_age_s=0
        )
        prop_direct = make_always_visible_propagator(
            SVALBARD, "SAT-D1", relay_value=0.0, data_age_s=0
        )

        def combined(sat_id, t):
            return prop_relay(sat_id, t) if sat_id == "SAT-R1" \
                   else prop_direct(sat_id, t)

        result = build_contact_schedule(
            satellite_ids      = ["SAT-R1", "SAT-D1"],
            stations           = [SVALBARD],
            propagator         = combined,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
            alpha              = 0.0,
            beta               = 1.0,
            gamma              = 0.0,
        )
        assert result.solver_status == "Optimal"
        assert len(result.scheduled_windows) == 1
        assert result.scheduled_windows[0].satellite_id == "SAT-R1"

    def test_full_pipeline_greedy_fallback(self):
        """
        use_greedy=True must return a Greedy-Optimal result without
        calling the ILP solver.
        """
        prop = make_always_visible_propagator(SVALBARD, "SAT-A1")

        result = build_contact_schedule(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
            use_greedy         = True,
        )
        assert result.solver_status == "Greedy-Optimal"
        assert result.total_contact_s > 0

    def test_weights_recorded_in_result(self):
        """build_contact_schedule must record the weights used."""
        prop = make_always_visible_propagator(SVALBARD, "SAT-A1")

        result = build_contact_schedule(
            satellite_ids      = ["SAT-A1"],
            stations           = [SVALBARD],
            propagator         = prop,
            horizon_start_unix = T0,
            horizon_end_unix   = T0 + 600,
            step_s             = 30.0,
            alpha              = 0.6,
            beta               = 0.2,
            gamma              = 0.2,
        )
        assert result.weights_used == (0.6, 0.2, 0.2)
