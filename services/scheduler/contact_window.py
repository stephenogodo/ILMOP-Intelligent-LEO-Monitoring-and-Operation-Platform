"""
services/scheduler/contact_window.py

Layer 2 — ILP Contact Window Scheduler.

Scans a planning horizon, identifies all satellite-ground station
visibility windows, detects conflicts (two satellites visible from
the same station simultaneously), and solves an Integer Linear
Programme to produce a conflict-free, maximum-duration contact
schedule.

Depends on Layer 1: services.scheduler.geometry
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
import pulp

from services.scheduler.geometry import (
    GroundStation,
    Observation,
    observe,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SatelliteState:
    """
    A satellite's position and velocity in ECI at one instant.
    In real use this comes from the SGP4 propagator.
    For the scheduler we accept any provider via a callable.
    """
    satellite_id:    str
    eci_position_km: np.ndarray   # shape (3,)
    eci_velocity_km_s: np.ndarray # shape (3,)


@dataclass
class ContactWindow:
    """
    One continuous interval during which a satellite is above the
    elevation mask at a ground station.
    """
    satellite_id:      str
    station:           GroundStation
    aos_unix:          float    # Acquisition of Signal, Unix timestamp
    los_unix:          float    # Loss of Signal, Unix timestamp
    max_elevation_deg: float    # peak elevation during the window
    duration_s:        float    # LOS − AOS in seconds
    scheduled:         bool = False   # set True by the ILP solver


@dataclass
class ScheduleResult:
    """
    The output of one scheduling run.
    """
    horizon_start_unix: float
    horizon_end_unix:   float
    all_windows:        list[ContactWindow]    # every visible window found
    scheduled_windows:  list[ContactWindow]    # conflict-free optimal subset
    dropped_windows:    list[ContactWindow]    # windows sacrificed to resolve conflicts
    total_contact_s:    float                  # sum of scheduled durations
    solver_status:      str                    # "Optimal", "Infeasible", etc.


# ---------------------------------------------------------------------------
# Step 1 — Visibility scan
# ---------------------------------------------------------------------------

# Type alias for the propagator callable:
#   propagator(satellite_id, unix_time) → SatelliteState
PropagatorFn = callable


def scan_visibility(
    satellite_ids:      list[str],
    stations:           list[GroundStation],
    propagator:         PropagatorFn,
    horizon_start_unix: float,
    horizon_end_unix:   float,
    step_s:             float = 30.0,
    elevation_mask_deg: float = 5.0,
) -> list[ContactWindow]:
    """
    Scan a planning horizon and return all contact windows.

    For each (satellite, station) pair, steps through the horizon
    in increments of step_s seconds. When the satellite crosses above
    the elevation mask, a window opens (AOS). When it drops below,
    the window closes (LOS). Each continuous visible interval becomes
    one ContactWindow.

    Parameters
    ----------
    satellite_ids       : list of satellite identifiers
    stations            : list of GroundStation objects
    propagator          : callable(sat_id, unix_time) → SatelliteState
    horizon_start_unix  : start of planning horizon, Unix timestamp
    horizon_end_unix    : end of planning horizon, Unix timestamp
    step_s              : time step for scanning in seconds (default 30 s)
    elevation_mask_deg  : minimum elevation for visibility (default 5°)

    Returns
    -------
    List of ContactWindow objects, one per continuous visible interval.

    Notes
    -----
    A 30-second step introduces at most 30 seconds of error in AOS/LOS
    timing. For scheduling purposes this is acceptable. For uplink
    commanding, a finer step (10 s or less) would be used.
    """
    windows: list[ContactWindow] = []

    for sat_id in satellite_ids:
        for station in stations:

            # State machine: track whether we are currently inside a window
            in_window       = False
            window_aos      = 0.0
            window_max_el   = -90.0

            t = horizon_start_unix

            while t <= horizon_end_unix:
                state = propagator(sat_id, t)
                obs   = observe(
                    sat_eci_km       = state.eci_position_km,
                    sat_vel_eci_km_s = state.eci_velocity_km_s,
                    unix_time_s      = t,
                    station          = station,
                    elevation_mask_deg = elevation_mask_deg,
                )

                if obs.above_mask and not in_window:
                    # Satellite just rose above the mask — window opens
                    in_window     = True
                    window_aos    = t
                    window_max_el = obs.elevation_deg

                elif obs.above_mask and in_window:
                    # Continuing window — track peak elevation
                    window_max_el = max(window_max_el, obs.elevation_deg)

                elif not obs.above_mask and in_window:
                    # Satellite just dropped below the mask — window closes
                    in_window = False
                    los       = t
                    duration  = los - window_aos

                    # Discard windows shorter than one time step
                    # (artefacts of the scan resolution)
                    if duration >= step_s:
                        windows.append(ContactWindow(
                            satellite_id      = sat_id,
                            station           = station,
                            aos_unix          = window_aos,
                            los_unix          = los,
                            max_elevation_deg = window_max_el,
                            duration_s        = duration,
                        ))

                t += step_s

            # Close any window still open at the end of the horizon
            if in_window:
                los      = horizon_end_unix
                duration = los - window_aos
                if duration >= step_s:
                    windows.append(ContactWindow(
                        satellite_id      = sat_id,
                        station           = station,
                        aos_unix          = window_aos,
                        los_unix          = los,
                        max_elevation_deg = window_max_el,
                        duration_s        = duration,
                    ))

    return windows


# ---------------------------------------------------------------------------
# Step 2 — Conflict detection
# ---------------------------------------------------------------------------

def find_conflicts(windows: list[ContactWindow]
                   ) -> list[list[int]]:
    """
    Find groups of windows that conflict — i.e. they overlap in time
    at the same ground station.

    Two windows conflict when:
      - They share the same ground station, AND
      - Their time intervals overlap:
          window_a.aos < window_b.los  AND  window_b.aos < window_a.los

    Parameters
    ----------
    windows : list of ContactWindow (indexed 0..N-1)

    Returns
    -------
    List of conflict groups. Each group is a list of window indices
    that are mutually conflicting. A window with no conflicts does not
    appear in any group.

    Notes
    -----
    This uses a sweep-line approach: for each station, sort windows
    by AOS time and check consecutive overlaps. Overlapping chains
    are merged into one conflict group.
    """
    from collections import defaultdict

    # Group windows by station name
    by_station: dict[str, list[int]] = defaultdict(list)
    for i, w in enumerate(windows):
        by_station[w.station.name].append(i)

    conflict_groups: list[list[int]] = []

    for station_name, indices in by_station.items():
        # Sort by AOS time
        indices.sort(key=lambda i: windows[i].aos_unix)

        # Sweep through and merge overlapping windows into groups
        current_group: list[int] = []
        group_los = -1.0   # the latest LOS in the current group

        for idx in indices:
            w = windows[idx]

            if not current_group:
                # Start a new group
                current_group = [idx]
                group_los     = w.los_unix

            elif w.aos_unix < group_los:
                # This window overlaps the current group — add to it
                current_group.append(idx)
                group_los = max(group_los, w.los_unix)

            else:
                # No overlap — close the current group if it had conflicts
                if len(current_group) > 1:
                    conflict_groups.append(current_group)
                # Start a fresh group
                current_group = [idx]
                group_los     = w.los_unix

        # Close the last group
        if len(current_group) > 1:
            conflict_groups.append(current_group)

    return conflict_groups


# ---------------------------------------------------------------------------
# Step 3 — ILP solver
# ---------------------------------------------------------------------------

def solve_schedule(
    windows:         list[ContactWindow],
    conflict_groups: list[list[int]],
) -> ScheduleResult:
    """
    Solve the contact window scheduling ILP.

    Decision variables
    ------------------
    x[w] ∈ {0, 1}  for each window w
      x[w] = 1 → schedule this window
      x[w] = 0 → drop this window

    Objective
    ---------
    Maximise total scheduled contact time:
      max  Σ_w  x[w] × duration_s[w]

    Constraints
    -----------
    For each conflict group C:
      Σ_{w ∈ C}  x[w]  ≤  1
    (at most one window per conflict group can be scheduled)

    Parameters
    ----------
    windows         : all ContactWindow objects from scan_visibility
    conflict_groups : output of find_conflicts

    Returns
    -------
    ScheduleResult with scheduled and dropped windows identified.
    """
    if not windows:
        return ScheduleResult(
            horizon_start_unix = 0.0,
            horizon_end_unix   = 0.0,
            all_windows        = [],
            scheduled_windows  = [],
            dropped_windows    = [],
            total_contact_s    = 0.0,
            solver_status      = "NoWindows",
        )

    # ── Build the ILP ────────────────────────────────────────────────────────
    prob = pulp.LpProblem("contact_window_schedule", pulp.LpMaximize)

    # One binary variable per window
    x = [
        pulp.LpVariable(f"x_{i}", 0, 1, "Binary")
        for i in range(len(windows))
    ]

    # Objective: maximise total contact duration
    prob += pulp.lpSum(
        x[i] * windows[i].duration_s
        for i in range(len(windows))
    )

    # Conflict constraints: at most one window per conflict group
    for group in conflict_groups:
        prob += pulp.lpSum(x[i] for i in group) <= 1

    # ── Solve ────────────────────────────────────────────────────────────────
    # Use CBC solver, suppress console output
    solver = pulp.PULP_CBC_CMD(msg=0)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]

    # ── Extract results ───────────────────────────────────────────────────────
    scheduled: list[ContactWindow] = []
    dropped:   list[ContactWindow] = []

    for i, w in enumerate(windows):
        val = pulp.value(x[i])
        if val is not None and val > 0.5:
            w.scheduled = True
            scheduled.append(w)
        else:
            w.scheduled = False
            dropped.append(w)

    total_s = sum(w.duration_s for w in scheduled)

    return ScheduleResult(
        horizon_start_unix = windows[0].aos_unix,
        horizon_end_unix   = windows[-1].los_unix,
        all_windows        = windows,
        scheduled_windows  = scheduled,
        dropped_windows    = dropped,
        total_contact_s    = total_s,
        solver_status      = status,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_contact_schedule(
    satellite_ids:      list[str],
    stations:           list[GroundStation],
    propagator:         PropagatorFn,
    horizon_start_unix: float,
    horizon_end_unix:   float,
    step_s:             float = 30.0,
    elevation_mask_deg: float = 5.0,
) -> ScheduleResult:
    """
    Full pipeline: scan → detect conflicts → solve ILP → return schedule.

    This is the single entry point for the scheduler. It combines
    all three steps into one call.

    Parameters
    ----------
    satellite_ids       : e.g. ["SAT-A1", "SAT-A2", "SAT-A3"]
    stations            : list of GroundStation objects
    propagator          : callable(sat_id, unix_time) → SatelliteState
    horizon_start_unix  : planning horizon start, Unix timestamp
    horizon_end_unix    : planning horizon end, Unix timestamp
    step_s              : scan resolution in seconds (default 30 s)
    elevation_mask_deg  : minimum elevation angle (default 5°)

    Returns
    -------
    ScheduleResult — the complete optimised contact schedule.
    """
    windows  = scan_visibility(
        satellite_ids, stations, propagator,
        horizon_start_unix, horizon_end_unix,
        step_s, elevation_mask_deg,
    )

    conflicts = find_conflicts(windows)

    result = solve_schedule(windows, conflicts)

    return result