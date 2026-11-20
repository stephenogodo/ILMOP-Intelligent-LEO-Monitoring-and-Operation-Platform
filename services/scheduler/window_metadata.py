"""
services/scheduler/window_metadata.py

S7-14 — scheduling metadata for the composite objective.

The composite score of a contact window uses two quantities that are carried
on SatelliteState and sampled at the window's AOS:

    relay_value : how useful the satellite is as an ISL relay at that instant
    data_age_s  : how long ago the satellite was last in view of a station

A plain SGP4 propagator supplies neither, so the beta and gamma terms were
always zero. `EnrichedPropagator` wraps a propagator and fills both in from
the constellation geometry.

Definitions
-----------
relay_value(s, t)
    Share (0-1) of the other satellites that have no ground station above the
    elevation mask at t but are reachable from s within `max_hops` ISL hops.
    A satellite that can reach many otherwise-dark satellites is a valuable
    contact: the ground link also drains their queues by relay.

data_age_s(s, t)
    Seconds since s was last above the elevation mask at any station.
    Before the horizon starts the age is assumed to be INITIAL_DATA_AGE_S.
    This is a geometric upper bound on freshness: it ignores which contacts
    the scheduler actually grants.

Both are computed on a fixed time grid (`step_s`, normally the scan step) and
looked up by the nearest grid index.
"""

from __future__ import annotations

from collections import defaultdict, deque

import numpy as np

from services.scheduler.contact_window import SatelliteState
from services.scheduler.geometry import GroundStation, observe
from services.scheduler.isl_visibility import compute_isl_topology

#: Assumed data age at the start of the horizon (earlier history not modelled).
INITIAL_DATA_AGE_S = 1800.0


def hop_reach(adjacency: dict, source: str, max_hops: int) -> dict[str, int]:
    """Satellites reachable from `source` within `max_hops` ISL hops -> hop count."""
    seen = {source: 0}
    queue = deque([source])
    while queue:
        u = queue.popleft()
        if seen[u] >= max_hops:
            continue
        for v in adjacency.get(u, ()):
            if v not in seen:
                seen[v] = seen[u] + 1
                queue.append(v)
    return seen


class EnrichedPropagator:
    """
    Propagator callable(sat_id, unix) -> SatelliteState with relay_value and
    data_age_s filled in. Drop-in for build_contact_schedule.

    Parameters
    ----------
    satellite_ids      : all satellites of the constellation
    stations           : ground stations
    base_propagator    : callable(sat_id, unix) -> SatelliteState (geometry only)
    orbit_classes      : {sat_id: 'LEO'|'HEO'} for per-class ISL range limits
                         (None -> one default range for every pair)
    horizon_start_unix / horizon_end_unix : precomputation span
    step_s             : grid step; use the scheduler's scan step
    """

    def __init__(
        self,
        satellite_ids:      list[str],
        stations:           list[GroundStation],
        base_propagator,
        horizon_start_unix: float,
        horizon_end_unix:   float,
        step_s:             float = 60.0,
        elevation_mask_deg: float = 5.0,
        max_hops:           int   = 3,
        orbit_classes:      dict | None = None,
    ):
        self.satellite_ids = list(satellite_ids)
        self.t0     = float(horizon_start_unix)
        self.step_s = float(step_s)
        self._n_steps = int(np.floor((horizon_end_unix - horizon_start_unix)
                                     / step_s)) + 1
        idx = {s: i for i, s in enumerate(self.satellite_ids)}
        n, T = len(self.satellite_ids), self._n_steps

        self._states: list[dict[str, SatelliteState]] = []
        self.adjacency: list[dict] = []     # ISL adjacency per grid step
        visible = np.zeros((n, T), dtype=bool)
        self.relay_value = np.zeros((n, T))
        self.data_age    = np.zeros((n, T))

        last_seen = {s: None for s in self.satellite_ids}
        for k in range(T):
            t = self.t0 + k * self.step_s
            states = {s: base_propagator(s, t) for s in self.satellite_ids}
            self._states.append(states)

            for s in self.satellite_ids:
                st = states[s]
                for station in stations:
                    if observe(
                        sat_eci_km=st.eci_position_km,
                        sat_vel_eci_km_s=st.eci_velocity_km_s,
                        unix_time_s=t, station=station,
                        elevation_mask_deg=elevation_mask_deg,
                    ).above_mask:
                        visible[idx[s], k] = True
                        break

            topo = compute_isl_topology(
                {s: states[s].eci_position_km for s in self.satellite_ids},
                t, orbit_classes=orbit_classes,
            )
            adj = defaultdict(set)
            for link in topo.visible_links:
                adj[link.sat_a].add(link.sat_b)
                adj[link.sat_b].add(link.sat_a)

            self.adjacency.append(dict(adj))

            for s in self.satellite_ids:
                i = idx[s]
                if visible[i, k]:
                    last_seen[s] = t
                self.data_age[i, k] = (
                    t - last_seen[s] if last_seen[s] is not None
                    else INITIAL_DATA_AGE_S + (t - self.t0)
                )
            # relay_value needs the visibility of every satellite at step k.
            for s in self.satellite_ids:
                reach = hop_reach(adj, s, max_hops)
                dark_reached = sum(1 for o in reach
                                   if o != s and not visible[idx[o], k])
                self.relay_value[idx[s], k] = (
                    dark_reached / (n - 1) if n > 1 else 0.0)

        self._idx = idx
        self.visible = visible

    # ------------------------------------------------------------------
    def _k(self, unix: float) -> int:
        k = int(round((unix - self.t0) / self.step_s))
        return min(max(k, 0), self._n_steps - 1)

    def adjacency_at(self, unix: float) -> dict:
        """ISL adjacency {sat_id: {neighbour ids}} at the nearest grid step."""
        return self.adjacency[self._k(unix)]

    def __call__(self, sat_id: str, unix: float) -> SatelliteState:
        k = self._k(unix)
        base = self._states[k][sat_id]
        i = self._idx[sat_id]
        return SatelliteState(
            satellite_id      = sat_id,
            eci_position_km   = base.eci_position_km,
            eci_velocity_km_s = base.eci_velocity_km_s,
            relay_value       = float(self.relay_value[i, k]),
            data_age_s        = float(self.data_age[i, k]),
        )
