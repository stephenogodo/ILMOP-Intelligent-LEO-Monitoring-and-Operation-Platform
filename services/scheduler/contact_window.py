"""
services/scheduler/contact_window.py

Layer 2 — ILP Contact Window Scheduler.

Scans a planning horizon, identifies all satellite-ground station
visibility windows, detects conflicts (two satellites visible from
the same station simultaneously), and solves an Integer Linear
Programme to produce a conflict-free, maximum-score contact schedule.

Composite objective (S7-14)
---------------------------
The ILP maximises a weighted sum of three terms per window:

    score(w) = α × duration_norm(w)
             + β × relay_value(w)
             + γ × data_age_norm(w)

where:

  α (alpha)  — weight for contact duration
               Longer passes earn more score.  Default 0.5.

  β (beta)   — weight for relay utility
               Satellites that are currently dark (no direct ground
               contact) and can serve as ISL relay nodes earn a higher
               relay_value (0–1).  A satellite with a direct link has
               relay_value = 0.  Default 0.3.

  γ (gamma)  — weight for data freshness
               data_age_s is the time in seconds since the satellite
               last had a ground contact.  Older data earns more score,
               ensuring dark/neglected satellites are prioritised.
               Default 0.2.

All three component scores are normalised to [0, 1] within the current
candidate window set before the weighted sum is formed, so the weights
are dimensionless and directly comparable.

When α + β + γ = 0 the solver falls back to equal weights (1/3 each).

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
    A satellite's position, velocity, and operational metadata at one instant.
    In real use this comes from the SGP4 propagator.
    For the scheduler we accept any provider via a callable.
    """
    satellite_id:      str
    eci_position_km:   np.ndarray    # shape (3,)
    eci_velocity_km_s: np.ndarray    # shape (3,)
    relay_value:       float = 0.0   # 0–1: ISL relay utility (0 = has direct link)
    data_age_s:        float = 0.0   # seconds since last ground contact


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
    relay_value:       float = 0.0   # copied from SatelliteState at scan time
    data_age_s:        float = 0.0   # copied from SatelliteState at scan time
    scheduled:         bool  = False  # set True by the ILP solver
    composite_score:   float = 0.0   # normalised weighted score


@dataclass
class ScheduleResult:
    """
    The output of one scheduling run.
    """
    horizon_start_unix:  float
    horizon_end_unix:    float
    all_windows:         list[ContactWindow]    # every visible window found
    scheduled_windows:   list[ContactWindow]    # conflict-free optimal subset
    dropped_windows:     list[ContactWindow]    # windows sacrificed to resolve conflicts
    total_contact_s:     float                  # sum of scheduled durations
    solver_status:       str                    # "Optimal", "Infeasible", etc.
    objective_value:     float = 0.0            # ILP objective value (composite)
    weights_used:        tuple[float, float, float] = (0.5, 0.3, 0.2)  # (α, β, γ)


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

    The satellite's relay_value and data_age_s are sampled once at
    AOS time and recorded on the ContactWindow for use by the ILP.

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
            window_relay    = 0.0
            window_data_age = 0.0

            t = horizon_start_unix

            while t <= horizon_end_unix:
                state = propagator(sat_id, t)
                obs   = observe(
                    sat_eci_km         = state.eci_position_km,
                    sat_vel_eci_km_s   = state.eci_velocity_km_s,
                    unix_time_s        = t,
                    station            = station,
                    elevation_mask_deg = elevation_mask_deg,
                )

                if obs.above_mask and not in_window:
                    # Satellite just rose above the mask — window opens
                    in_window       = True
                    window_aos      = t
                    window_max_el   = obs.elevation_deg
                    # Sample relay metadata at AOS
                    window_relay    = float(state.relay_value)
                    window_data_age = float(state.data_age_s)

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
                            relay_value       = window_relay,
                            data_age_s        = window_data_age,
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
                        relay_value       = window_relay,
                        data_age_s        = window_data_age,
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
# Step 2b — Composite score computation
# ---------------------------------------------------------------------------

def _compute_composite_scores(
    windows: list[ContactWindow],
    alpha:   float,
    beta:    float,
    gamma:   float,
) -> list[float]:
    """
    Compute a normalised composite score for each window.

    score(w) = α × duration_norm(w)
             + β × relay_value(w)          [already in 0–1]
             + γ × data_age_norm(w)

    duration_norm and data_age_norm are min-max normalised across the
    candidate window set so all three components are on [0, 1].

    relay_value is already expected to be in [0, 1] (set by the ISL
    relay coordinator or defaulting to 0).

    If the total weight α + β + γ = 0, equal weights (1/3 each) are used.

    Parameters
    ----------
    windows : list of ContactWindow
    alpha   : weight for duration term
    beta    : weight for relay_value term
    gamma   : weight for data_age term

    Returns
    -------
    List of composite scores, parallel to windows.
    """
    if not windows:
        return []

    total_weight = alpha + beta + gamma
    if total_weight == 0.0:
        alpha = beta = gamma = 1.0 / 3.0

    # Extract raw values
    durations  = [w.duration_s  for w in windows]
    relays     = [w.relay_value for w in windows]
    ages       = [w.data_age_s  for w in windows]

    def _minmax_norm(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        if hi == lo:
            return [0.5] * len(values)
        return [(v - lo) / (hi - lo) for v in values]

    dur_norm  = _minmax_norm(durations)
    age_norm  = _minmax_norm(ages)
    # relay_value is already in [0,1]; clamp defensively
    rel_norm  = [max(0.0, min(1.0, r)) for r in relays]

    # Apply a small epsilon floor so every window has a strictly positive
    # objective contribution. Without this, a zero-score non-conflicting
    # window would be arbitrarily dropped by the solver (indifferent to
    # including or excluding it), which is never the desired behaviour.
    _EPS = 1e-6
    scores = [
        max(_EPS, alpha * d + beta * r + gamma * a)
        for d, r, a in zip(dur_norm, rel_norm, age_norm)
    ]

    return scores


# ---------------------------------------------------------------------------
# Step 3 — ILP solver (composite objective)
# ---------------------------------------------------------------------------

def solve_schedule(
    windows:         list[ContactWindow],
    conflict_groups: list[list[int]],
    alpha:           float = 0.5,
    beta:            float = 0.3,
    gamma:           float = 0.2,
) -> ScheduleResult:
    """
    Solve the contact window scheduling ILP with a composite objective.

    Decision variables
    ------------------
    x[w] ∈ {0, 1}  for each window w
      x[w] = 1 → schedule this window
      x[w] = 0 → drop this window

    Objective (S7-14)
    -----------------
    Maximise the weighted composite score across scheduled windows:

      max  Σ_w  x[w] × score(w)

    where

      score(w) = α × duration_norm(w)
               + β × relay_value_norm(w)
               + γ × data_age_norm(w)

    All components are normalised to [0, 1] before weighting.

    Constraints
    -----------
    For each conflict group C:
      Σ_{w ∈ C}  x[w]  ≤  1
    (at most one window per conflict group can be scheduled)

    Parameters
    ----------
    windows         : all ContactWindow objects from scan_visibility
    conflict_groups : output of find_conflicts
    alpha           : duration weight (default 0.5)
    beta            : relay utility weight (default 0.3)
    gamma           : data age weight (default 0.2)

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
            objective_value    = 0.0,
            weights_used       = (alpha, beta, gamma),
        )

    # ── Normalise and store composite scores ─────────────────────────────────
    scores = _compute_composite_scores(windows, alpha, beta, gamma)
    for w, s in zip(windows, scores):
        w.composite_score = s

    # Resolve effective weights (handles the all-zero edge case)
    total_weight = alpha + beta + gamma
    if total_weight == 0.0:
        eff_alpha = eff_beta = eff_gamma = 1.0 / 3.0
    else:
        eff_alpha, eff_beta, eff_gamma = alpha, beta, gamma

    # ── Build the ILP ────────────────────────────────────────────────────────
    prob = pulp.LpProblem("contact_window_schedule", pulp.LpMaximize)

    # One binary variable per window
    x = [
        pulp.LpVariable(f"x_{i}", 0, 1, "Binary")
        for i in range(len(windows))
    ]

    # Objective: maximise total composite score
    prob += pulp.lpSum(
        x[i] * scores[i]
        for i in range(len(windows))
    )

    # Conflict constraints: at most one window per conflict group
    for group in conflict_groups:
        prob += pulp.lpSum(x[i] for i in group) <= 1

    # ── Solve ────────────────────────────────────────────────────────────────
    solver = pulp.PULP_CBC_CMD(msg=0)
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    obj_val = pulp.value(prob.objective) or 0.0

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
        objective_value    = obj_val,
        weights_used       = (eff_alpha, eff_beta, eff_gamma),
    )


# ---------------------------------------------------------------------------
# Greedy fallback (used when ILP times out)
# ---------------------------------------------------------------------------

def greedy_schedule(
    windows:         list[ContactWindow],
    conflict_groups: list[list[int]],
    alpha:           float = 0.5,
    beta:            float = 0.3,
    gamma:           float = 0.2,
) -> ScheduleResult:
    """
    Greedy fallback scheduler — same composite score, no ILP.

    Within each conflict group, greedily picks the window with the
    highest composite score. Non-conflicting windows are always accepted.

    Used when the ILP solver times out on large constellations.

    Parameters
    ----------
    windows         : all ContactWindow objects
    conflict_groups : output of find_conflicts
    alpha, beta, gamma : composite objective weights

    Returns
    -------
    ScheduleResult — may be sub-optimal but always terminates quickly.
    """
    if not windows:
        return ScheduleResult(
            horizon_start_unix = 0.0,
            horizon_end_unix   = 0.0,
            all_windows        = [],
            scheduled_windows  = [],
            dropped_windows    = [],
            total_contact_s    = 0.0,
            solver_status      = "Greedy-NoWindows",
            objective_value    = 0.0,
            weights_used       = (alpha, beta, gamma),
        )

    scores = _compute_composite_scores(windows, alpha, beta, gamma)
    for w, s in zip(windows, scores):
        w.composite_score = s

    # Build set of conflicted indices
    conflicted: set[int] = set()
    for group in conflict_groups:
        conflicted.update(group)

    selected: set[int] = set()

    # For each conflict group, pick the highest-score window
    for group in conflict_groups:
        best_idx = max(group, key=lambda i: scores[i])
        selected.add(best_idx)

    # Accept all non-conflicting windows
    for i in range(len(windows)):
        if i not in conflicted:
            selected.add(i)

    scheduled = [windows[i] for i in sorted(selected)]
    dropped   = [windows[i] for i in range(len(windows)) if i not in selected]

    for w in scheduled:
        w.scheduled = True
    for w in dropped:
        w.scheduled = False

    total_s  = sum(w.duration_s for w in scheduled)
    obj_val  = sum(w.composite_score for w in scheduled)

    total_weight = alpha + beta + gamma
    if total_weight == 0.0:
        eff_alpha = eff_beta = eff_gamma = 1.0 / 3.0
    else:
        eff_alpha, eff_beta, eff_gamma = alpha, beta, gamma

    return ScheduleResult(
        horizon_start_unix = windows[0].aos_unix,
        horizon_end_unix   = windows[-1].los_unix,
        all_windows        = windows,
        scheduled_windows  = scheduled,
        dropped_windows    = dropped,
        total_contact_s    = total_s,
        solver_status      = "Greedy-Optimal",
        objective_value    = obj_val,
        weights_used       = (eff_alpha, eff_beta, eff_gamma),
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
    alpha:              float = 0.5,
    beta:               float = 0.3,
    gamma:              float = 0.2,
    use_greedy:         bool  = False,
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
    alpha               : duration weight (default 0.5)
    beta                : relay utility weight (default 0.3)
    gamma               : data age weight (default 0.2)
    use_greedy          : if True, skip ILP and use the greedy fallback

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

    if use_greedy:
        result = greedy_schedule(windows, conflicts, alpha, beta, gamma)
    else:
        result = solve_schedule(windows, conflicts, alpha, beta, gamma)

    return result
