"""
tests/test_window_metadata.py

S7-14 — relay_value / data_age_s metadata and the duration-weighted
composite objective.
"""

import inspect
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.dashboard.panel_data import (
    build_orbit_models, make_propagator, make_stations,
)
from services.scheduler.contact_window import (
    ContactWindow, _compute_composite_scores, build_contact_schedule,
    find_exclusion_groups, solve_schedule,
)
from services.scheduler.geometry import GroundStation
from services.scheduler.window_metadata import (
    INITIAL_DATA_AGE_S, EnrichedPropagator, hop_reach,
)

NOW = 1790071200.0
SG = GroundStation('SG', 78.2, 15.6, 0.0)

HEO = {'n_planes': 2, 'sats_per_plane': 1, 'altitude_km': 20200.0,
       'inclination_deg': 63.4, 'raan_base': 0.0, 'raan_step': 180.0,
       'ma_plane_step': 180.0, 'eccentricity': 0.74, 'arg_perigee': 270.0}
LEO = {'n_planes': 1, 'sats_per_plane': 4, 'altitude_km': 550.0,
       'inclination_deg': 53.0, 'raan_base': 0.0, 'raan_step': 90.0,
       'eccentricity': 0.001, 'arg_perigee': 0.0}
CFG = dict(HEO, extra_shells=[LEO])
STATIONS = {'Svalbard': {'lat': 78.229, 'lon': 15.608, 'alt': 24.0},
            'Houston':  {'lat': 29.76, 'lon': -95.37, 'alt': 15.0}}


def _slots():
    out = []
    for k, shell in enumerate([CFG] + CFG['extra_shells']):
        prefix = '' if k == 0 else f'X{k}'
        for p in range(shell['n_planes']):
            raan = shell['raan_base'] + p * shell['raan_step']
            for s in range(shell['sats_per_plane']):
                ma = (p * shell.get('ma_plane_step', 0.0)
                      + s * 360.0 / shell['sats_per_plane']) % 360.0
                out.append((f'{prefix}P{p+1}S{s+1}', raan, ma, shell))
    return out


@pytest.fixture(scope='module')
def enriched():
    slots = _slots()
    models, classes = build_orbit_models(CFG, slots)
    ids = [s[0] for s in slots]
    prop = EnrichedPropagator(ids, make_stations(STATIONS),
                              make_propagator(models), NOW, NOW + 7200,
                              step_s=60.0, orbit_classes=classes)
    return ids, make_propagator(models), prop


# ── hop_reach ─────────────────────────────────────────────────────────────────

def test_hop_reach_respects_the_hop_limit():
    adj = {'a': {'b'}, 'b': {'a', 'c'}, 'c': {'b', 'd'}, 'd': {'c'}}
    assert hop_reach(adj, 'a', 2) == {'a': 0, 'b': 1, 'c': 2}
    assert hop_reach(adj, 'a', 3)['d'] == 3
    assert hop_reach(adj, 'a', 0) == {'a': 0}


def test_hop_reach_isolated_satellite():
    assert hop_reach({}, 'x', 3) == {'x': 0}


# ── EnrichedPropagator ────────────────────────────────────────────────────────

def test_geometry_is_unchanged(enriched):
    ids, base, prop = enriched
    for sid in ids:
        a, b = base(sid, NOW + 600), prop(sid, NOW + 600)
        assert np.allclose(a.eci_position_km, b.eci_position_km)


def test_relay_value_in_unit_interval(enriched):
    ids, _, prop = enriched
    assert (prop.relay_value >= 0).all() and (prop.relay_value <= 1).all()
    assert prop.relay_value.max() > 0       # somebody can relay for somebody


def test_data_age_zero_when_visible_and_grows_otherwise(enriched):
    ids, _, prop = enriched
    i = 0
    vis, age = prop.visible[i], prop.data_age[i]
    assert (age[vis] == 0.0).all()
    for k in range(1, len(vis)):
        if not vis[k] and not vis[k - 1] and age[k - 1] != 0:
            assert age[k] == pytest.approx(age[k - 1] + 60.0)


def test_data_age_starts_from_assumed_initial_age(enriched):
    ids, _, prop = enriched
    k0_dark = [i for i in range(len(ids)) if not prop.visible[i, 0]]
    assert k0_dark
    for i in k0_dark:
        assert prop.data_age[i, 0] == pytest.approx(INITIAL_DATA_AGE_S)


def test_times_outside_the_grid_are_clamped(enriched):
    ids, _, prop = enriched
    assert prop(ids[0], NOW - 1e5).data_age_s == prop(ids[0], NOW).data_age_s
    assert prop(ids[0], NOW + 1e6).satellite_id == ids[0]


def test_enriched_propagator_drives_the_scheduler(enriched):
    ids, _, prop = enriched
    res = build_contact_schedule(ids, make_stations(STATIONS), prop,
                                 NOW, NOW + 7200, step_s=60.0)
    assert res.scheduled_windows
    assert any(w.relay_value > 0 for w in res.all_windows)


# ── duration-weighted objective ───────────────────────────────────────────────

def _w(sat, aos, los, relay=0.0, age=0.0):
    return ContactWindow(sat, SG, aos, los, 45.0, los - aos,
                         relay_value=relay, data_age_s=age)


def test_additive_objective_prefers_two_short_windows_over_one_long_one():
    """The behaviour this change fixes: the additive score ignores how long a
    window is, so the ILP packs in as many windows as it can."""
    long_ = _w('L', 0, 3000, relay=0.5)
    s1, s2 = _w('S1', 0, 400, relay=0.5), _w('S2', 2600, 3000, relay=0.5)
    ws = [long_, s1, s2]
    res = solve_schedule(ws, find_exclusion_groups(ws), 0.2, 0.6, 0.0)
    assert {w.satellite_id for w in res.scheduled_windows} == {'S1', 'S2'}


def test_duration_weighted_objective_prefers_the_long_window():
    long_ = _w('L', 0, 3000, relay=0.5)
    s1, s2 = _w('S1', 0, 400, relay=0.5), _w('S2', 2600, 3000, relay=0.5)
    ws = [long_, s1, s2]
    res = solve_schedule(ws, find_exclusion_groups(ws), 0.2, 0.6, 0.0,
                         duration_weighted=True)
    assert {w.satellite_id for w in res.scheduled_windows} == {'L'}


def test_duration_weighted_score_scales_with_duration():
    a, b = _w('A', 0, 1000), _w('B', 0, 500)
    sa, sb = _compute_composite_scores([a, b], 1.0, 0.0, 0.0,
                                       duration_weighted=True)
    assert sa == pytest.approx(2 * sb)


def test_relay_weight_changes_the_choice_at_equal_duration():
    a = _w('A', 0, 1000, relay=0.9)
    b = _w('B', 0, 1000, relay=0.1)
    ws = [a, b]
    res = solve_schedule(ws, find_exclusion_groups(ws), 0.2, 0.6, 0.2,
                         duration_weighted=True)
    assert [w.satellite_id for w in res.scheduled_windows] == ['A']


def test_pipeline_default_is_duration_weighted_and_low_level_default_is_not():
    assert inspect.signature(build_contact_schedule) \
        .parameters['duration_weighted'].default is True
    assert inspect.signature(solve_schedule) \
        .parameters['duration_weighted'].default is False
