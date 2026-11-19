"""
tests/test_panel_data.py

S7-15 — tests for services/dashboard/panel_data.py (handover, dark-satellite
and relay panel data). Streamlit-free; the greedy scheduler is used so the
tests do not depend on the PuLP version.
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.dashboard.panel_data import (
    INITIAL_DATA_AGE_S, QUEUE_CAP_MB, QUEUE_GROWTH_MB_S,
    build_orbit_models, build_panel_context, build_panel_snapshot,
    data_age_s, eci_state, handover_state_at, horizon_bucket, queue_mb,
    shell_orbit_class,
)
from services.scheduler.contact_window import ContactWindow
from services.scheduler.geometry import GroundStation
from services.scheduler.handover import HandoverState, build_handover_plan
from services.scheduler.relay_coordinator import MAX_RELAY_HOPS

HEO = {'n_planes': 6, 'sats_per_plane': 1, 'altitude_km': 20200.0,
       'inclination_deg': 63.4, 'raan_base': 0.0, 'raan_step': 60.0,
       'ma_plane_step': 60.0, 'eccentricity': 0.74, 'arg_perigee': 270.0}
LEO = {'n_planes': 2, 'sats_per_plane': 6, 'altitude_km': 550.0,
       'inclination_deg': 53.0, 'raan_base': 0.0, 'raan_step': 90.0,
       'eccentricity': 0.001, 'arg_perigee': 0.0}
SCENARIO = dict(HEO, extra_shells=[LEO])

STATIONS = {
    'Svalbard': {'lat': 78.229, 'lon': 15.608, 'alt': 24.0},
    'Houston':  {'lat': 29.760, 'lon': -95.370, 'alt': 15.0},
    'Tokyo':    {'lat': 35.690, 'lon': 139.692, 'alt': 40.0},
}
NOW = 1790071200.0   # 2026-09-22 10:00 UTC


def slots(cfg):
    out = []
    for k, shell in enumerate([cfg] + list(cfg.get('extra_shells', []))):
        prefix = '' if k == 0 else f'X{k}'
        for p in range(shell['n_planes']):
            raan = shell['raan_base'] + p * shell['raan_step']
            for s in range(shell['sats_per_plane']):
                ma = (p * shell.get('ma_plane_step', 0.0)
                      + s * 360.0 / shell['sats_per_plane']) % 360.0
                out.append((f'{prefix}P{p+1}S{s+1}', raan, ma, shell))
    return out


@pytest.fixture(scope='module')
def snapshot():
    ctx = build_panel_context(SCENARIO, slots(SCENARIO), STATIONS,
                              horizon_bucket(NOW), horizon_h=3.0,
                              step_s=120.0, use_greedy=True)
    return ctx, build_panel_snapshot(ctx, NOW)


# ── orbit helpers ─────────────────────────────────────────────────────────────

def test_shell_orbit_class():
    assert shell_orbit_class(HEO) == 'HEO'
    assert shell_orbit_class(LEO) == 'LEO'


def test_models_cover_18_satellites_6_heo_12_leo():
    models, classes = build_orbit_models(SCENARIO, slots(SCENARIO))
    assert len(models) == 18
    assert sum(c == 'HEO' for c in classes.values()) == 6
    assert sum(c == 'LEO' for c in classes.values()) == 12


def test_heo_radius_stays_between_perigee_and_apogee():
    models, classes = build_orbit_models(SCENARIO, slots(SCENARIO))
    sid = next(s for s, c in classes.items() if c == 'HEO')
    radii = [np.linalg.norm(eci_state(models[sid], NOW + k * 600)[0])
             for k in range(0, 72)]
    assert min(radii) >= 6371 + 500 - 50
    assert max(radii) <= 6371 + 39750 + 50
    assert max(radii) > 30000       # actually reaches the high-altitude part


def test_leo_radius_is_circular():
    models, classes = build_orbit_models(SCENARIO, slots(SCENARIO))
    sid = next(s for s, c in classes.items() if c == 'LEO')
    r = np.linalg.norm(eci_state(models[sid], NOW)[0])
    assert r == pytest.approx(6371 + 550, abs=15)


# ── handover state / data age ─────────────────────────────────────────────────

def _win(sat, station, aos, los):
    return ContactWindow(sat, station, aos, los, 40.0, los - aos, scheduled=True)


def test_handover_state_idle_active_released():
    gs_a = GroundStation('A', 0, 0, 0)
    plan = build_handover_plan('S', [_win('S', gs_a, 1000.0, 2000.0)])
    state, st_, nxt = handover_state_at(plan, 500.0)
    assert state == HandoverState.IDLE and st_ is None and nxt is not None
    state, st_, _ = handover_state_at(plan, 1500.0)
    assert state in (HandoverState.ACTIVE, HandoverState.ACQUIRING)
    assert st_.name == 'A'
    state, st_, nxt = handover_state_at(plan, 5000.0)
    assert state in (HandoverState.RELEASED, HandoverState.IDLE)
    assert st_ is None and nxt is None


def test_data_age_resets_in_contact_and_counts_from_los():
    gs = GroundStation('A', 0, 0, 0)
    w = [_win('S', gs, 1000.0, 2000.0)]
    assert data_age_s('S', 1500.0, 0.0, w) == 0.0
    assert data_age_s('S', 2600.0, 0.0, w) == pytest.approx(600.0)
    # no contact yet: assumed initial age plus time since horizon start
    assert data_age_s('S', 500.0, 0.0, w) == pytest.approx(500.0 + INITIAL_DATA_AGE_S)


def test_queue_model_grows_and_caps():
    assert queue_mb(100.0) == pytest.approx(100.0 * QUEUE_GROWTH_MB_S)
    assert queue_mb(1e9) == QUEUE_CAP_MB


def test_horizon_bucket_is_hour_aligned_and_not_after_now():
    b = horizon_bucket(NOW + 1234.0)
    assert b % 3600 == 0 and b <= NOW + 1234.0 < b + 3600


# ── snapshot ──────────────────────────────────────────────────────────────────

def test_snapshot_has_a_row_per_satellite(snapshot):
    _, s = snapshot
    assert len(s.handover) == len(s.dark) == 18
    assert {r.orbit_class for r in s.handover} == {'HEO', 'LEO'}


def test_dark_rows_are_consistent(snapshot):
    _, s = snapshot
    for r in s.dark:
        assert 0.0 <= r.dark_fraction <= 1.0
        assert 0.0 <= r.queue_mb <= QUEUE_CAP_MB
        assert r.data_age_s >= 0.0
    assert set(s.dark_ids) <= {r.satellite_id for r in s.dark}


def test_in_contact_satellites_are_not_dark_and_have_zero_age(snapshot):
    ctx, s = snapshot
    for w in ctx.schedule.scheduled_windows:
        if w.aos_unix <= NOW < w.los_unix:
            row = next(r for r in s.dark if r.satellite_id == w.satellite_id)
            assert not row.is_dark and row.data_age_s == 0.0


def test_relay_sessions_only_serve_dark_satellites_within_hop_limit(snapshot):
    _, s = snapshot
    dark = set()
    for rr in s.relay:
        ses = rr.session
        assert ses.contact_satellite_id == rr.contact_satellite
        assert not set(ses.dark_serviced) & set(ses.dark_unreachable)
        for c in ses.collections:
            assert c.relay_path[0] == rr.contact_satellite
            assert len(c.relay_path) - 1 <= MAX_RELAY_HOPS
            assert c.relay_path[-1] != rr.contact_satellite
        assert ses.total_relayed_mb == pytest.approx(
            sum(c.relayed_mb for c in ses.collections))
        assert ses.relay_time_used_s <= rr.session.window_duration_s + 1e-6


def test_snapshot_reuses_context_cheaply_for_different_times(snapshot):
    ctx, _ = snapshot
    a = build_panel_snapshot(ctx, NOW)
    b = build_panel_snapshot(ctx, NOW + 1800.0)
    assert a.horizon_start == b.horizon_start
    assert a.now_unix != b.now_unix
    assert len(b.handover) == 18


def test_isl_topology_uses_class_ranges(snapshot):
    _, s = snapshot
    for link in s.isl_topology.visible_links:
        a, b = s.orbit_classes[link.sat_a], s.orbit_classes[link.sat_b]
        if a == b == 'LEO':
            assert link.range_km <= 5000.0


def test_scheduled_windows_never_overlap_at_a_station_or_for_a_satellite(snapshot):
    ctx, _ = snapshot
    sch = ctx.schedule.scheduled_windows
    for i, a in enumerate(sch):
        for b in sch[i + 1:]:
            if a.aos_unix < b.los_unix and b.aos_unix < a.los_unix:
                assert a.station.name != b.station.name
                assert a.satellite_id != b.satellite_id


def test_seven_station_schedule_is_not_throttled_by_chained_conflicts():
    """Regression for S7-15: chain-merged conflict groups scheduled 8 of 120
    windows (greedy and ILP alike); with pairwise exclusion the 7-station
    scenario schedules more than 10 % of its windows even with the greedy
    solver (17-29 / 120 measured; the ILP schedules 68 / 120)."""
    stations = dict(STATIONS)
    stations.update({
        'Cambridge': {'lat': 52.205, 'lon': 0.119, 'alt': 20.0},
        'Lagos':     {'lat': 6.454, 'lon': 3.395, 'alt': 41.0},
        'Sydney':    {'lat': -33.865, 'lon': 151.21, 'alt': 20.0},
        'Fairbanks': {'lat': 64.838, 'lon': -147.716, 'alt': 136.0},
    })
    ctx = build_panel_context(SCENARIO, slots(SCENARIO), stations,
                              horizon_bucket(NOW), use_greedy=True)
    sched = ctx.schedule
    assert len(sched.scheduled_windows) > 0.1 * len(sched.all_windows)


def test_concurrent_relay_sessions_do_not_collect_the_same_satellite_twice(snapshot):
    _, s = snapshot
    seen = set()
    for rr in s.relay:
        if rr.window_active:
            serviced = set(rr.session.dark_serviced)
            assert not serviced & seen
            seen |= serviced
