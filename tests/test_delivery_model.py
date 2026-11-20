"""tests/test_delivery_model.py — capacity / latency model for relay-and-forward."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.scheduler.contact_window import ContactWindow
from services.scheduler.delivery_model import simulate_delivery
from services.scheduler.geometry import GroundStation

GS = GroundStation('GS', 0.0, 0.0, 0.0)
T0 = 1_000_000.0


def win(sat, aos_step, los_step, dt=60.0):
    return ContactWindow(sat, GS, T0 + aos_step * dt, T0 + los_step * dt,
                         45.0, (los_step - aos_step) * dt, scheduled=True)


CHAIN = {'A': {'B'}, 'B': {'A', 'C'}, 'C': {'B'}}


def run(windows, **kw):
    args = dict(satellite_ids=['A', 'B', 'C'], scheduled_windows=windows,
                adjacency_at=lambda t: CHAIN, t0=T0, n_steps=60, dt=60.0,
                gen_mbps=0.8, ground_mbps=8.0, isl_mbps=100.0)
    args.update(kw)
    return simulate_delivery(**args)


def test_mass_is_conserved():
    r = run([win('A', 10, 40)])
    assert r.generated_mb == pytest.approx(r.delivered_mb + r.backlog_mb)


def test_no_windows_delivers_nothing():
    r = run([])
    assert r.delivered_mb == 0 and r.backlog_mb == pytest.approx(r.generated_mb)


def test_without_relay_only_the_contact_satellite_is_served():
    r = run([win('A', 0, 60)], relay=False)
    assert r.relayed_mb == 0
    assert {s for s, _, _ in r.latencies_s} == {'A'}


def test_relay_serves_satellites_within_the_hop_limit():
    r = run([win('A', 0, 60)], max_hops=2)
    assert {s for s, _, _ in r.latencies_s} == {'A', 'B', 'C'}
    r1 = run([win('A', 0, 60)], max_hops=1)
    assert {s for s, _, _ in r1.latencies_s} == {'A', 'B'}


def test_relay_improves_delivery():
    a = run([win('A', 0, 60)], relay=False)
    b = run([win('A', 0, 60)], relay=True)
    assert b.delivered_mb > a.delivered_mb
    assert b.relay_share_pct > 0


def test_ground_capacity_limits_delivery():
    low = run([win('A', 0, 60)], ground_mbps=0.5)
    high = run([win('A', 0, 60)], ground_mbps=50.0)
    assert low.delivered_mb < high.delivered_mb
    # one contact satellite cannot downlink more than its window allows
    assert low.delivered_mb <= 0.5 / 8 * 60 * 60 + 1e-6


def test_isl_intake_limits_relayed_volume():
    r = run([win('A', 0, 60)], isl_mbps=0.8, ground_mbps=80.0)
    assert r.relayed_mb <= 0.8 / 8 * 60 * 60 + 1e-6


def test_latency_is_non_negative_and_fifo():
    r = run([win('A', 30, 60)], relay=False)
    lats = [l for s, l, _ in r.latencies_s if s == 'A']
    assert all(l > 0 for l in lats)
    # older data (generated earlier) waits longer
    assert lats[0] > lats[-1]
    assert r.latency_p95_min() >= r.latency_mean_min() > 0


def test_each_dark_satellite_is_served_by_one_contact_per_step():
    two = {'A': {'B'}, 'B': {'A', 'C'}, 'C': {'B'}}
    r = run([win('A', 0, 60), win('C', 0, 60)], adjacency_at=lambda t: two)
    assert r.generated_mb == pytest.approx(r.delivered_mb + r.backlog_mb)
