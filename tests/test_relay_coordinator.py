"""
tests/test_relay_coordinator.py

Unit and integration tests for Layer 6 — ISL Store-and-Forward Relay
Coordinator (services/scheduler/relay_coordinator.py).

Test taxonomy
-------------
  T1  No dark satellites                    → empty RelaySession
  T2  Dark satellite unreachable (no ISL)   → dark_unreachable populated
  T3  Single dark, direct one-hop relay     → correct relayed_mb / time
  T4  Multi-hop BFS path (2 hops)           → relay_path length = 3
  T5  Priority ordering (age vs queue)      → highest-priority served first
  T6  Capacity exhaustion (window too short)→ dark_skipped non-empty
  T7  relay_value output on RelaySession    → relay_time_used_s correct
  T8  Zero-queue satellite skipped          → not in dark_serviced
  T9  Contact satellite excluded from dark  → not collected from self
  T10 Batch schedule (coordinate_relay_for_schedule) — two windows
  T11 Single-candidate normalisation (0.5 fallback)
  T12 _bfs_paths max_hops limit             → satellite 3 hops away excluded
"""

from __future__ import annotations

import math
from collections import defaultdict
from unittest.mock import patch

import numpy as np
import pytest

from services.scheduler.geometry import GroundStation
from services.scheduler.contact_window import ContactWindow
from services.scheduler.isl_visibility import ISLLink, ISLTopology
from services.scheduler.relay_coordinator import (
    ISL_RATE_MBPS,
    MAX_RELAY_HOPS,
    DarkSatelliteProfile,
    RelayCollection,
    RelaySession,
    W_AGE,
    W_HOP,
    W_QUEUE,
    _bfs_paths,
    _compute_priorities,
    coordinate_relay,
    coordinate_relay_for_schedule,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

STATION = GroundStation(name="Lagos", lat_deg=6.5, lon_deg=3.4, alt_km=0.0)

T0 = 1_700_000_000.0   # arbitrary AOS epoch


def make_window(
    sat_id: str,
    duration_s: float = 300.0,
    aos: float = T0,
    relay_value: float = 0.0,
    data_age_s: float = 0.0,
) -> ContactWindow:
    return ContactWindow(
        satellite_id      = sat_id,
        station           = STATION,
        aos_unix          = aos,
        los_unix          = aos + duration_s,
        max_elevation_deg = 45.0,
        duration_s        = duration_s,
        relay_value       = relay_value,
        data_age_s        = data_age_s,
        scheduled         = True,
    )


def make_topology(
    links: list[tuple[str, str]],
    unix_time: float = T0,
    all_sats: list[str] | None = None,
) -> ISLTopology:
    isl_links = []
    for a, b in links:
        sa, sb = (a, b) if a < b else (b, a)
        isl_links.append(ISLLink(sat_a=sa, sat_b=sb, range_km=1000.0, is_visible=True))
    return ISLTopology(unix_time=unix_time, links=isl_links)


def _data_age(values: dict[str, float]):
    return lambda sid: values.get(sid, 0.0)


def _queue(values: dict[str, float]):
    return lambda sid: values.get(sid, 0.0)


# ---------------------------------------------------------------------------
# T1  No dark satellites
# ---------------------------------------------------------------------------

class TestNoDarkSatellites:
    def test_empty_session_returned(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B", "SAT-C"]
        scheduled = [
            make_window("SAT-A", aos=T0),
            make_window("SAT-B", aos=T0 + 60),
            make_window("SAT-C", aos=T0 + 120),
        ]
        topology = make_topology([("SAT-A", "SAT-B"), ("SAT-B", "SAT-C")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({}),
            queue_size_fn     = _queue({"SAT-B": 50.0, "SAT-C": 80.0}),
            dark_horizon_s    = 600.0,
        )

        assert session.contact_satellite_id == "SAT-A"
        assert session.collections == []
        assert session.total_relayed_mb == 0.0
        assert session.relay_time_used_s == 0.0
        assert session.dark_serviced == []
        assert session.dark_unreachable == []
        assert session.dark_skipped == []
        assert math.isclose(session.window_time_remaining_s, 300.0)


# ---------------------------------------------------------------------------
# T2  Dark satellite unreachable
# ---------------------------------------------------------------------------

class TestDarkUnreachable:
    def test_unreachable_satellite_logged(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 800.0}),
            queue_size_fn     = _queue({"SAT-B": 100.0}),
            dark_horizon_s    = 600.0,
        )

        assert "SAT-B" in session.dark_unreachable
        assert session.collections == []
        assert session.total_relayed_mb == 0.0


# ---------------------------------------------------------------------------
# T3  Single dark satellite, direct one-hop relay
# ---------------------------------------------------------------------------

class TestDirectOneHopRelay:
    def test_correct_relayed_mb_and_time(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 900.0}),
            queue_size_fn     = _queue({"SAT-B": 100.0}),
            isl_rate_mbps     = 100.0,
            dark_horizon_s    = 600.0,
        )

        assert len(session.collections) == 1
        col = session.collections[0]
        assert col.satellite_id == "SAT-B"
        assert math.isclose(col.relayed_mb, 100.0, rel_tol=1e-6)
        assert math.isclose(col.time_used_s, 8.0, rel_tol=1e-6)
        assert col.queue_remaining_mb == 0.0
        assert col.relay_path == ["SAT-A", "SAT-B"]
        assert "SAT-B" in session.dark_serviced
        assert math.isclose(session.total_relayed_mb, 100.0, rel_tol=1e-6)

    def test_isl_rate_carried_on_session(self):
        contact_window = make_window("SAT-A", duration_s=60.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 10.0}),
            isl_rate_mbps     = 50.0,
            dark_horizon_s    = 600.0,
        )
        assert session.isl_rate_mbps == 50.0


# ---------------------------------------------------------------------------
# T4  Multi-hop BFS path (2 hops)
# ---------------------------------------------------------------------------

class TestMultiHopBFS:
    def test_two_hop_path(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B", "SAT-C"]
        scheduled = [
            make_window("SAT-A", aos=T0),
            make_window("SAT-B", aos=T0 + 30),
        ]
        topology = make_topology([("SAT-A", "SAT-B"), ("SAT-B", "SAT-C")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-C": 700.0}),
            queue_size_fn     = _queue({"SAT-C": 50.0}),
            dark_horizon_s    = 600.0,
        )

        assert len(session.collections) == 1
        col = session.collections[0]
        assert col.satellite_id == "SAT-C"
        assert col.relay_path == ["SAT-A", "SAT-B", "SAT-C"]

    def test_bfs_paths_unit(self):
        topology = make_topology([
            ("SAT-A", "SAT-B"),
            ("SAT-B", "SAT-C"),
            ("SAT-C", "SAT-D"),
        ])
        paths = _bfs_paths("SAT-A", topology, max_hops=3)

        assert paths["SAT-B"] == ["SAT-A", "SAT-B"]
        assert paths["SAT-C"] == ["SAT-A", "SAT-B", "SAT-C"]
        assert paths["SAT-D"] == ["SAT-A", "SAT-B", "SAT-C", "SAT-D"]
        assert "SAT-A" not in paths


# ---------------------------------------------------------------------------
# T5  Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_high_age_served_first(self):
        contact_window = make_window("SAT-A", duration_s=600.0)
        all_sats = ["SAT-A", "SAT-B", "SAT-C"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B"), ("SAT-A", "SAT-C")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 2000.0, "SAT-C": 100.0}),
            queue_size_fn     = _queue({"SAT-B": 5.0, "SAT-C": 500.0}),
            w_age=0.5, w_queue=0.3, w_hop=0.2,
            dark_horizon_s    = 600.0,
        )

        assert len(session.collections) >= 1
        assert session.collections[0].satellite_id == "SAT-B"

    def test_compute_priorities_descending(self):
        profiles = [
            DarkSatelliteProfile("X", data_age_s=100.0, queue_size_mb=10.0, hop_count=1, relay_path=[]),
            DarkSatelliteProfile("Y", data_age_s=900.0, queue_size_mb=50.0, hop_count=1, relay_path=[]),
            DarkSatelliteProfile("Z", data_age_s=500.0, queue_size_mb=25.0, hop_count=2, relay_path=[]),
        ]
        result = _compute_priorities(profiles)
        priorities = [p.priority for p in result]
        assert priorities == sorted(priorities, reverse=True)

    def test_all_priorities_in_unit_range(self):
        profiles = [
            DarkSatelliteProfile("A", data_age_s=0.0,    queue_size_mb=0.0,    hop_count=1, relay_path=[]),
            DarkSatelliteProfile("B", data_age_s=3600.0, queue_size_mb=1000.0, hop_count=1, relay_path=[]),
            DarkSatelliteProfile("C", data_age_s=1800.0, queue_size_mb=500.0,  hop_count=3, relay_path=[]),
        ]
        _compute_priorities(profiles)
        for p in profiles:
            assert 0.0 <= p.priority <= 1.0


# ---------------------------------------------------------------------------
# T6  Capacity exhaustion
# ---------------------------------------------------------------------------

class TestCapacityExhaustion:
    def test_partial_collection_when_window_runs_out(self):
        contact_window = make_window("SAT-A", duration_s=1.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 1000.0}),
            isl_rate_mbps     = 100.0,
            dark_horizon_s    = 600.0,
        )

        assert len(session.collections) == 1
        col = session.collections[0]
        expected_mb = 1.0 * (100.0 / 8.0)   # 12.5 MB
        assert math.isclose(col.relayed_mb, expected_mb, rel_tol=1e-6)
        assert col.queue_remaining_mb > 0.0
        assert math.isclose(session.window_time_remaining_s, 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# T7  Window time tracking
# ---------------------------------------------------------------------------

class TestWindowTimeTracking:
    def test_relay_time_used_sums_correctly(self):
        contact_window = make_window("SAT-A", duration_s=600.0)
        all_sats = ["SAT-A", "SAT-B", "SAT-C"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B"), ("SAT-A", "SAT-C")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 500.0, "SAT-C": 500.0}),
            queue_size_fn     = _queue({"SAT-B": 50.0, "SAT-C": 30.0}),
            dark_horizon_s    = 600.0,
        )

        expected_time = sum(c.time_used_s for c in session.collections)
        assert math.isclose(session.relay_time_used_s, expected_time, rel_tol=1e-9)

    def test_window_time_remaining_is_consistent(self):
        contact_window = make_window("SAT-A", duration_s=200.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 25.0}),
            dark_horizon_s    = 600.0,
        )

        total = session.relay_time_used_s + session.window_time_remaining_s
        assert math.isclose(total, 200.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# T8  Zero-queue satellite skipped
# ---------------------------------------------------------------------------

class TestZeroQueueSkipped:
    def test_zero_queue_not_in_serviced(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 0.0}),
            dark_horizon_s    = 600.0,
        )

        assert "SAT-B" not in session.dark_serviced
        assert session.total_relayed_mb == 0.0


# ---------------------------------------------------------------------------
# T9  Contact satellite excluded
# ---------------------------------------------------------------------------

class TestContactSatelliteExcluded:
    def test_contact_sat_not_self_collected(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A"]
        scheduled = []
        topology = make_topology([])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-A": 9000.0}),
            queue_size_fn     = _queue({"SAT-A": 500.0}),
            dark_horizon_s    = 600.0,
        )

        assert "SAT-A" not in session.dark_serviced
        assert session.collections == []


# ---------------------------------------------------------------------------
# T10  Batch schedule
# ---------------------------------------------------------------------------

class TestBatchSchedule:
    def test_two_windows_two_sessions(self):
        w_a = make_window("SAT-A", duration_s=300.0, aos=T0)
        w_b = make_window("SAT-B", duration_s=300.0, aos=T0 + 400)
        scheduled_windows = [w_a, w_b]
        all_sats = ["SAT-A", "SAT-B", "SAT-C"]

        def isl_topology_fn(t: float) -> ISLTopology:
            if t <= T0 + 200:
                return make_topology([("SAT-A", "SAT-C")])
            return make_topology([("SAT-B", "SAT-C")])

        sessions = coordinate_relay_for_schedule(
            scheduled_windows = scheduled_windows,
            all_satellite_ids = all_sats,
            isl_topology_fn   = isl_topology_fn,
            data_age_fn       = lambda sid, t: {"SAT-C": 700.0}.get(sid, 0.0),
            queue_size_fn     = lambda sid, t: {"SAT-C": 50.0}.get(sid, 0.0),
            dark_horizon_s    = 600.0,
        )

        assert len(sessions) == 2
        sats_served = {c.satellite_id for s in sessions for c in s.collections}
        assert "SAT-C" in sats_served

    def test_sessions_ordered_by_aos(self):
        w1 = make_window("SAT-A", duration_s=300.0, aos=T0 + 200)
        w2 = make_window("SAT-B", duration_s=300.0, aos=T0)

        sessions = coordinate_relay_for_schedule(
            scheduled_windows = [w1, w2],
            all_satellite_ids = ["SAT-A", "SAT-B"],
            isl_topology_fn   = lambda t: make_topology([]),
            data_age_fn       = lambda sid, t: 0.0,
            queue_size_fn     = lambda sid, t: 0.0,
            dark_horizon_s    = 600.0,
        )

        aos_times = [s.window_start_unix for s in sessions]
        assert aos_times == sorted(aos_times)


# ---------------------------------------------------------------------------
# T11  Single-candidate normalisation (0.5 fallback)
# ---------------------------------------------------------------------------

class TestSingleCandidateNormalisation:
    def test_single_profile_gets_0_5_per_component(self):
        profiles = [
            DarkSatelliteProfile("X", data_age_s=500.0, queue_size_mb=100.0, hop_count=1, relay_path=[])
        ]
        result = _compute_priorities(profiles)
        expected = W_AGE * 0.5 + W_QUEUE * 0.5 + W_HOP * 0.5
        assert math.isclose(result[0].priority, expected, rel_tol=1e-9)

    def test_single_candidate_integrated(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 500.0}),
            queue_size_fn     = _queue({"SAT-B": 50.0}),
            dark_horizon_s    = 600.0,
        )
        assert "SAT-B" in session.dark_serviced


# ---------------------------------------------------------------------------
# T12  _bfs_paths max_hops limit
# ---------------------------------------------------------------------------

class TestBFSMaxHops:
    def test_satellite_beyond_max_hops_excluded(self):
        topology = make_topology([
            ("SAT-A", "SAT-B"),
            ("SAT-B", "SAT-C"),
            ("SAT-C", "SAT-D"),
        ])
        paths = _bfs_paths("SAT-A", topology, max_hops=2)
        assert "SAT-D" not in paths
        assert "SAT-C" in paths
        assert "SAT-B" in paths

    def test_max_hops_1_direct_only(self):
        topology = make_topology([("SAT-A", "SAT-B"), ("SAT-B", "SAT-C")])
        paths = _bfs_paths("SAT-A", topology, max_hops=1)
        assert "SAT-B" in paths
        assert "SAT-C" not in paths

    def test_coordinator_respects_max_hops(self):
        contact_window = make_window("SAT-A", duration_s=600.0)
        all_sats = ["SAT-A", "SAT-B", "SAT-C", "SAT-D"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([
            ("SAT-A", "SAT-B"),
            ("SAT-B", "SAT-C"),
            ("SAT-C", "SAT-D"),
        ])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0, "SAT-C": 700.0, "SAT-D": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 50.0,  "SAT-C": 50.0,  "SAT-D": 50.0}),
            max_hops          = 1,
            dark_horizon_s    = 600.0,
        )

        assert "SAT-B" in session.dark_serviced
        assert "SAT-C" not in session.dark_serviced
        assert "SAT-D" not in session.dark_serviced


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_fleet(self):
        contact_window = make_window("SAT-A", duration_s=300.0)
        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = [],
            scheduled_windows = [],
            isl_topology      = make_topology([]),
            data_age_fn       = _data_age({}),
            queue_size_fn     = _queue({}),
            dark_horizon_s    = 600.0,
        )
        assert session.collections == []

    def test_zero_duration_window(self):
        contact_window = make_window("SAT-A", duration_s=0.0)
        all_sats = ["SAT-A", "SAT-B"]
        scheduled = [make_window("SAT-A", aos=T0)]
        topology = make_topology([("SAT-A", "SAT-B")])

        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = all_sats,
            scheduled_windows = scheduled,
            isl_topology      = topology,
            data_age_fn       = _data_age({"SAT-B": 700.0}),
            queue_size_fn     = _queue({"SAT-B": 100.0}),
            dark_horizon_s    = 600.0,
        )
        assert session.total_relayed_mb == 0.0

    def test_session_metadata(self):
        contact_window = make_window("SAT-X", duration_s=120.0)
        session = coordinate_relay(
            contact_window    = contact_window,
            all_satellite_ids = ["SAT-X"],
            scheduled_windows = [make_window("SAT-X", aos=T0)],
            isl_topology      = make_topology([]),
            data_age_fn       = _data_age({}),
            queue_size_fn     = _queue({}),
            dark_horizon_s    = 600.0,
        )
        assert session.contact_satellite_id == "SAT-X"
        assert session.station_name == "Lagos"
        assert math.isclose(session.window_duration_s, 120.0)
        assert math.isclose(session.window_start_unix, T0)
