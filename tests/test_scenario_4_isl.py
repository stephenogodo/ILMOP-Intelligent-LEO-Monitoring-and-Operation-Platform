"""
tests/test_scenario_4_isl.py

S7-16: ISL topology of the Scenario 4 hybrid constellation, using the
simulator's own SGP4 orbits and the per-orbit-class range limits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import jday

from services.satellite_simulator.constellation import ConstellationConfig
from services.satellite_simulator.orbit import OrbitModel
from services.scheduler.isl_visibility import compute_isl_topology

SCENARIO_4 = "config/constellations/scenario_4_hybrid_heo_leo.yaml"


def _topologies(step_min=60, hours=24):
    cfg = ConstellationConfig.from_yaml(SCENARIO_4)
    models = {
        s.satellite_id: OrbitModel(
            altitude_km=s.altitude_km, inclination_deg=s.inclination_deg,
            raan_deg=s.raan_deg, mean_anomaly_deg=s.mean_anomaly_deg,
            arg_perigee_deg=s.arg_perigee_deg,
            apogee_altitude_km=s.apogee_altitude_km,
        )
        for s in cfg.satellites
    }
    yr, mo, dy = next(iter(models.values()))._EPOCH
    t0 = datetime(yr, mo, dy, tzinfo=timezone.utc)
    classes = cfg.orbit_classes()
    out = []
    for k in range(0, hours * 60, step_min):
        t = t0 + timedelta(minutes=k)
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
        pos = {sid: np.array(m._sat.sgp4(jd, fr)[1]) for sid, m in models.items()}
        out.append(compute_isl_topology(pos, t.timestamp(), orbit_classes=classes))
    return cfg, classes, out


def test_every_leo_satellite_has_an_heo_relay_at_most_epochs():
    """HEO hubs reachable from LEO satellites most of the time (measured about 84 %; the
    rest are LEO satellites on the far side of the Earth from all HEO apogees)."""
    cfg, classes, topologies = _topologies()
    leo = [s for s, c in classes.items() if c == "LEO"]
    served = total = 0
    for top in topologies:
        for sid in leo:
            total += 1
            if any(classes[n] == "HEO" for n in top.reachable_from(sid)):
                served += 1
    assert served / total > 0.7


def test_leo_leo_links_respect_5000_km_limit():
    cfg, classes, topologies = _topologies()
    for top in topologies:
        for link in top.visible_links:
            if classes[link.sat_a] == "LEO" and classes[link.sat_b] == "LEO":
                assert link.range_km <= 5_000.0


def test_no_link_exceeds_45000_km():
    cfg, classes, topologies = _topologies()
    for top in topologies:
        for link in top.visible_links:
            assert link.range_km <= 45_000.0
