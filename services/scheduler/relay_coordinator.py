"""
services/scheduler/relay_coordinator.py

Layer 6 — ISL Store-and-Forward Relay Coordinator.

When a satellite acquires a ground station (AOS event), this layer:

  1. Identifies which satellites are currently dark (Layer 5).
  2. Determines which dark satellites are reachable over the ISL graph
     from the contact satellite, using BFS (single or multi-hop).
  3. Computes a composite priority score for each reachable dark satellite:

         priority(d) = w_age  * age_norm(d)
                     + w_queue * queue_norm(d)
                     + w_hop   * hop_inv_norm(d)

     where:
       age_norm    — normalised time since last ground contact (older → higher)
       queue_norm  — normalised queued data volume (more data → higher)
       hop_inv_norm — normalised inverse hop-count (fewer hops → higher)

     All three components are min-max normalised to [0, 1] across the
     candidate set before the weighted sum is formed.

  4. Allocates relay bandwidth greedily in priority order, subject to:
       - remaining_contact_window_s  (time left in the current AOS event)
       - ISL_RATE_MBPS               (link rate per ISL hop, default 100 Mbps)
       - A per-satellite queue_size_mb cap

  5. Returns a RelaySession describing every collection action taken,
     total data relayed (MB), and the remaining dark satellites that
     could not be reached.

Design references
-----------------
  ADR-020 (ILP scheduler), ADR-S7-3 (relay coordinator design).
  Paper candidates: P-S7-3, P-S7-4.

Depends on:
  Layer 3: services.scheduler.isl_visibility  (ISLTopology)
  Layer 5: services.scheduler.dark_satellite  (find_dark_satellites)
  Layer 2: services.scheduler.contact_window  (ContactWindow)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from services.scheduler.contact_window import ContactWindow
from services.scheduler.dark_satellite import find_dark_satellites
from services.scheduler.isl_visibility import ISLTopology

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default ISL link rate in Megabits per second.
ISL_RATE_MBPS: float = 100.0

#: Default maximum hop count for BFS relay path search.
MAX_RELAY_HOPS: int = 3

#: Priority weight for data age (staleness urgency).
W_AGE: float = 0.5

#: Priority weight for queue depth (backlog urgency).
W_QUEUE: float = 0.3

#: Priority weight for inverse hop count (link efficiency).
W_HOP: float = 0.2


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DarkSatelliteProfile:
    """
    Operational snapshot of one dark satellite at the time of an AOS event.

    Attributes
    ----------
    satellite_id    : satellite identifier
    data_age_s      : seconds since last successful ground contact
    queue_size_mb   : data queued for downlink, in megabytes
    hop_count       : BFS hop distance from the contact satellite
    relay_path      : ordered list of satellite IDs from contact→dark
                      (inclusive of both endpoints)
    priority        : composite priority score in [0, 1]; higher = more urgent
    """
    satellite_id:  str
    data_age_s:    float
    queue_size_mb: float
    hop_count:     int
    relay_path:    list[str]
    priority:      float = 0.0


@dataclass
class RelayCollection:
    """
    Record of a single relay collection action: one dark satellite serviced
    during one contact window.

    Attributes
    ----------
    satellite_id      : dark satellite that was collected from
    relayed_mb        : data volume successfully relayed, in MB
    time_used_s       : relay time consumed for this collection
    relay_path        : ISL path used (contact sat → ... → dark sat)
    priority          : composite priority at time of scheduling
    queue_remaining_mb: data still in queue after collection (may be > 0
                        if the window ran out of time)
    """
    satellite_id:       str
    relayed_mb:         float
    time_used_s:        float
    relay_path:         list[str]
    priority:           float
    queue_remaining_mb: float


@dataclass
class RelaySession:
    """
    Complete record of relay activity for one contact window (AOS→LOS).

    Attributes
    ----------
    contact_satellite_id : the satellite in ground contact
    station_name         : the ground station name
    window_duration_s    : planned contact window length (s)
    window_start_unix    : AOS epoch, Unix timestamp
    collections          : ordered list of RelayCollection (priority order)
    total_relayed_mb     : sum of relayed_mb across all collections
    relay_time_used_s    : total relay time consumed
    window_time_remaining_s : time left in window after all collections
    dark_serviced        : satellite IDs successfully collected from
    dark_unreachable     : dark satellites with no ISL path to contact sat
    dark_skipped         : dark satellites reachable but not collected
                           (window time exhausted)
    isl_rate_mbps        : ISL link rate used for capacity calculations
    """
    contact_satellite_id:    str
    station_name:            str
    window_duration_s:       float
    window_start_unix:       float
    collections:             list[RelayCollection] = field(default_factory=list)
    total_relayed_mb:        float = 0.0
    relay_time_used_s:       float = 0.0
    window_time_remaining_s: float = 0.0
    dark_serviced:           list[str] = field(default_factory=list)
    dark_unreachable:        list[str] = field(default_factory=list)
    dark_skipped:            list[str] = field(default_factory=list)
    isl_rate_mbps:           float = ISL_RATE_MBPS


# ---------------------------------------------------------------------------
# BFS path finder
# ---------------------------------------------------------------------------

def _bfs_paths(
    source:    str,
    topology:  ISLTopology,
    max_hops:  int,
) -> dict[str, list[str]]:
    """
    BFS from ``source`` over the ISL topology adjacency graph.

    Returns a dict mapping each reachable satellite_id (excluding
    ``source``) to the shortest path [source, …, target].

    Parameters
    ----------
    source    : starting satellite ID (the contact satellite)
    topology  : ISLTopology snapshot at AOS time
    max_hops  : maximum hop depth to explore

    Returns
    -------
    dict[satellite_id, path_list]
        path_list is ordered [source, intermediate…, target].
        Only reachable nodes within max_hops are included.
    """
    paths: dict[str, list[str]] = {}
    queue: deque = deque()
    queue.append((source, [source]))
    visited: set[str] = {source}

    while queue:
        current, path = queue.popleft()
        if len(path) - 1 >= max_hops:
            continue
        for neighbour in topology.reachable_from(current):
            if neighbour not in visited:
                visited.add(neighbour)
                new_path = path + [neighbour]
                paths[neighbour] = new_path
                queue.append((neighbour, new_path))

    return paths


# ---------------------------------------------------------------------------
# Priority scorer
# ---------------------------------------------------------------------------

def _compute_priorities(
    profiles: list[DarkSatelliteProfile],
    w_age:   float = W_AGE,
    w_queue: float = W_QUEUE,
    w_hop:   float = W_HOP,
) -> list[DarkSatelliteProfile]:
    """
    Compute min-max normalised composite priority for each profile.

    Modifies each profile's ``priority`` field in place and returns the
    same list sorted descending by priority.

    Components
    ----------
    age_norm    : data_age_s (higher age → higher urgency)
    queue_norm  : queue_size_mb (larger queue → higher urgency)
    hop_inv_norm: 1/hop_count (fewer hops → higher relay efficiency)

    Each component is min-max normalised to [0, 1] across the candidate
    set.  If all values are equal the normalised component is 0.5
    (avoids division by zero).
    """
    if not profiles:
        return profiles

    def _minmax(values: list[float]) -> list[float]:
        lo, hi = min(values), max(values)
        if math.isclose(lo, hi):
            return [0.5] * len(values)
        return [(v - lo) / (hi - lo) for v in values]

    ages      = [p.data_age_s    for p in profiles]
    queues    = [p.queue_size_mb for p in profiles]
    hop_invs  = [1.0 / max(p.hop_count, 1) for p in profiles]

    age_n   = _minmax(ages)
    queue_n = _minmax(queues)
    hop_n   = _minmax(hop_invs)

    for i, profile in enumerate(profiles):
        profile.priority = (
            w_age   * age_n[i]
            + w_queue * queue_n[i]
            + w_hop   * hop_n[i]
        )

    profiles.sort(key=lambda p: p.priority, reverse=True)
    return profiles


# ---------------------------------------------------------------------------
# Core relay coordinator
# ---------------------------------------------------------------------------

def coordinate_relay(
    contact_window:    ContactWindow,
    all_satellite_ids: list[str],
    scheduled_windows: list[ContactWindow],
    isl_topology:      ISLTopology,
    data_age_fn:       Callable[[str], float],
    queue_size_fn:     Callable[[str], float],
    dark_horizon_s:    float = 600.0,
    isl_rate_mbps:     float = ISL_RATE_MBPS,
    max_hops:          int   = MAX_RELAY_HOPS,
    w_age:             float = W_AGE,
    w_queue:           float = W_QUEUE,
    w_hop:             float = W_HOP,
) -> RelaySession:
    """
    Execute one relay coordination cycle at the start of a contact window.

    Called at AOS time.  Queries the dark satellite list, finds BFS paths
    from the contact satellite, scores candidates by composite priority,
    and greedily allocates contact-window time to relay collections.

    Parameters
    ----------
    contact_window    : the window that just opened (AOS event)
    all_satellite_ids : complete fleet satellite ID list
    scheduled_windows : full ILP schedule (to determine dark status)
    isl_topology      : ISL visibility snapshot at AOS time
    data_age_fn       : callable(satellite_id) → float (seconds since last
                        ground contact for that satellite)
    queue_size_fn     : callable(satellite_id) → float (MB queued for DL)
    dark_horizon_s    : lookahead horizon for dark detection (s)
    isl_rate_mbps     : ISL link throughput (Mbps)
    max_hops          : maximum BFS hops to explore
    w_age, w_queue, w_hop : priority weights (need not sum to 1)

    Returns
    -------
    RelaySession with full collection records and summary statistics.
    """
    contact_sat   = contact_window.satellite_id
    station_name  = contact_window.station.name
    window_dur_s  = contact_window.duration_s
    window_start  = contact_window.aos_unix

    session = RelaySession(
        contact_satellite_id    = contact_sat,
        station_name            = station_name,
        window_duration_s       = window_dur_s,
        window_start_unix       = window_start,
        isl_rate_mbps           = isl_rate_mbps,
        window_time_remaining_s = window_dur_s,
    )

    # Step 1: identify dark satellites (excluding the contact satellite)
    dark_ids = [
        s for s in find_dark_satellites(
            satellite_ids     = all_satellite_ids,
            epoch_unix        = window_start,
            scheduled_windows = scheduled_windows,
            horizon_s         = dark_horizon_s,
        )
        if s != contact_sat
    ]

    if not dark_ids:
        logger.info(
            "RelayCoordinator | %s@%s | no dark satellites in fleet",
            contact_sat, station_name,
        )
        return session

    # Step 2: BFS from contact satellite over ISL topology
    reachable = _bfs_paths(contact_sat, isl_topology, max_hops)

    unreachable = [s for s in dark_ids if s not in reachable]
    reachable_dark = [s for s in dark_ids if s in reachable]

    session.dark_unreachable = unreachable

    if not reachable_dark:
        logger.info(
            "RelayCoordinator | %s@%s | %d dark satellites, none ISL-reachable",
            contact_sat, station_name, len(dark_ids),
        )
        return session

    # Step 3: build profiles and score
    profiles = [
        DarkSatelliteProfile(
            satellite_id  = sat_id,
            data_age_s    = data_age_fn(sat_id),
            queue_size_mb = queue_size_fn(sat_id),
            hop_count     = len(reachable[sat_id]) - 1,   # hops = path length - 1
            relay_path    = reachable[sat_id],
        )
        for sat_id in reachable_dark
    ]

    profiles = _compute_priorities(profiles, w_age, w_queue, w_hop)

    logger.info(
        "RelayCoordinator | %s@%s | window=%.0fs | candidates=%d | "
        "unreachable=%d | rate=%.0f Mbps",
        contact_sat, station_name, window_dur_s,
        len(profiles), len(unreachable), isl_rate_mbps,
    )

    # Step 4: greedy capacity allocation
    time_remaining_s = window_dur_s

    for profile in profiles:
        if time_remaining_s <= 0.0:
            session.dark_skipped.append(profile.satellite_id)
            continue

        queue_mb = profile.queue_size_mb
        if queue_mb <= 0.0:
            # Nothing to collect from this satellite
            session.dark_skipped.append(profile.satellite_id)
            continue

        # Time needed to collect the full queue over ISL
        # rate_mb_per_s = isl_rate_mbps / 8.0  (Mbps → MB/s)
        rate_mb_s    = isl_rate_mbps / 8.0
        time_needed  = queue_mb / rate_mb_s

        # Clamp to available window time
        time_alloc   = min(time_needed, time_remaining_s)
        relayed_mb   = time_alloc * rate_mb_s
        remaining_q  = max(0.0, queue_mb - relayed_mb)

        collection = RelayCollection(
            satellite_id       = profile.satellite_id,
            relayed_mb         = relayed_mb,
            time_used_s        = time_alloc,
            relay_path         = profile.relay_path,
            priority           = profile.priority,
            queue_remaining_mb = remaining_q,
        )

        session.collections.append(collection)
        session.dark_serviced.append(profile.satellite_id)
        session.total_relayed_mb  += relayed_mb
        session.relay_time_used_s += time_alloc
        time_remaining_s          -= time_alloc

        logger.debug(
            "  Collected %s | %.1f MB | %.1fs | hops=%d | priority=%.3f",
            profile.satellite_id, relayed_mb, time_alloc,
            profile.hop_count, profile.priority,
        )

    session.window_time_remaining_s = time_remaining_s

    logger.info(
        "RelayCoordinator | %s@%s | relayed=%.1f MB | time_used=%.0fs "
        "| serviced=%d | skipped=%d | unreachable=%d",
        contact_sat, station_name,
        session.total_relayed_mb, session.relay_time_used_s,
        len(session.dark_serviced), len(session.dark_skipped),
        len(session.dark_unreachable),
    )

    return session


# ---------------------------------------------------------------------------
# Convenience: batch over an entire schedule
# ---------------------------------------------------------------------------

def coordinate_relay_for_schedule(
    scheduled_windows: list[ContactWindow],
    all_satellite_ids: list[str],
    isl_topology_fn:   Callable[[float], ISLTopology],
    data_age_fn:       Callable[[str, float], float],
    queue_size_fn:     Callable[[str, float], float],
    dark_horizon_s:    float = 600.0,
    isl_rate_mbps:     float = ISL_RATE_MBPS,
    max_hops:          int   = MAX_RELAY_HOPS,
    w_age:             float = W_AGE,
    w_queue:           float = W_QUEUE,
    w_hop:             float = W_HOP,
) -> list[RelaySession]:
    """
    Run relay coordination for every window in a schedule.

    At each AOS event, a fresh ISL topology snapshot and per-satellite
    state are obtained via the provided callables.

    Parameters
    ----------
    scheduled_windows : full list of scheduled ContactWindow objects
    all_satellite_ids : complete fleet satellite ID list
    isl_topology_fn   : callable(unix_time) → ISLTopology at that epoch
    data_age_fn       : callable(satellite_id, unix_time) → float (s)
    queue_size_fn     : callable(satellite_id, unix_time) → float (MB)
    dark_horizon_s    : dark detection lookahead (s)
    isl_rate_mbps     : ISL throughput (Mbps)
    max_hops          : BFS depth limit
    w_age, w_queue, w_hop : priority weights

    Returns
    -------
    List of RelaySession, one per scheduled window, in AOS order.
    """
    windows_sorted = sorted(scheduled_windows, key=lambda w: w.aos_unix)
    sessions: list[RelaySession] = []

    for window in windows_sorted:
        t_aos     = window.aos_unix
        topology  = isl_topology_fn(t_aos)

        session = coordinate_relay(
            contact_window    = window,
            all_satellite_ids = all_satellite_ids,
            scheduled_windows = scheduled_windows,
            isl_topology      = topology,
            data_age_fn       = lambda sid, t=t_aos: data_age_fn(sid, t),
            queue_size_fn     = lambda sid, t=t_aos: queue_size_fn(sid, t),
            dark_horizon_s    = dark_horizon_s,
            isl_rate_mbps     = isl_rate_mbps,
            max_hops          = max_hops,
            w_age             = w_age,
            w_queue           = w_queue,
            w_hop             = w_hop,
        )
        sessions.append(session)

    return sessions
