"""
tests/constellation_test.py

Tests for ConstellationManager and ConstellationConfig.
"""

import os
import pytest
from services.satellite_simulator.constellation import (
    ConstellationConfig,
    ConstellationManager,
)
from services.satellite_simulator.telemetry import FaultType

SCENARIO_FILES = {
    1: "config/constellations/scenario_1_single.yaml",
    2: "config/constellations/scenario_2_single_orbit.yaml",
    3: "config/constellations/scenario_3_multi_orbit.yaml",
    4: "config/constellations/scenario_4_hybrid_heo_leo.yaml",
}


# ── ConstellationConfig tests ─────────────────────────────────────────────────

def test_scenario_1_loads_correctly():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[1])
    assert config.scenario == 1
    assert config.satellite_count == 1
    assert config.orbit_type == "LEO_CIRCULAR"

def test_scenario_2_loads_correctly():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[2])
    assert config.scenario == 2
    assert config.satellite_count == 6
    assert config.orbit_type == "LEO_CIRCULAR"

def test_scenario_3_loads_correctly():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    assert config.scenario == 3
    assert config.satellite_count == 24
    assert config.orbit_type == "LEO_CIRCULAR"

def test_scenario_4_loads_correctly():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    assert config.scenario == 4
    assert config.satellite_count == 18
    assert config.orbit_type == "HYBRID"

def test_scenario_3_has_four_planes():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    plane_ids = {sat.plane_id for sat in config.satellites}
    assert plane_ids == {"A", "B", "C", "D"}

def test_scenario_3_satellites_evenly_spaced():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    plane_a = [s for s in config.satellites if s.plane_id == "A"]
    assert len(plane_a) == 6
    anomalies = [s.mean_anomaly_deg for s in plane_a]
    for i in range(1, len(anomalies)):
        assert abs(anomalies[i] - anomalies[i-1] - 60.0) < 0.01

def test_satellite_ids_are_unique():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    ids = [sat.satellite_id for sat in config.satellites]
    assert len(ids) == len(set(ids))

def test_all_leo_scenarios_have_correct_orbit_type():
    for scenario in [1, 2, 3]:
        config = ConstellationConfig.from_yaml(SCENARIO_FILES[scenario])
        for sat in config.satellites:
            assert sat.orbit_type == "LEO_CIRCULAR"

def test_scenario_4_has_both_orbit_types_per_satellite():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    heo = [s for s in config.satellites if s.orbit_type == "HEO_MOLNIYA"]
    leo = [s for s in config.satellites if s.orbit_type == "LEO_CIRCULAR"]
    assert len(heo) == 6 and len(leo) == 12


# ── ConstellationManager tests ────────────────────────────────────────────────

def test_manager_generates_correct_satellite_count():
    config  = ConstellationConfig.from_yaml(SCENARIO_FILES[2])
    manager = ConstellationManager(config)
    assert manager.satellite_count == 6

def test_manager_generate_all_returns_all_satellites():
    config    = ConstellationConfig.from_yaml(SCENARIO_FILES[2])
    manager   = ConstellationManager(config)
    snapshots = manager.generate_all()
    assert len(snapshots) == 6

def test_manager_all_snapshots_have_correct_orbit_type():
    config    = ConstellationConfig.from_yaml(SCENARIO_FILES[2])
    manager   = ConstellationManager(config)
    snapshots = manager.generate_all()
    for sat_id, telemetry in snapshots.items():
        assert telemetry.orbit_type == "LEO_CIRCULAR"

def test_manager_scenario_4_telemetry_keeps_per_satellite_orbit_type():
    config    = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    manager   = ConstellationManager(config)
    snapshots = manager.generate_all()
    assert len(snapshots) == 18
    assert {t.orbit_type for t in snapshots.values()} == {"LEO_CIRCULAR", "HEO_MOLNIYA"}
    for sat in config.satellites:
        assert snapshots[sat.satellite_id].orbit_type == sat.orbit_type

def test_manager_with_time_multiplier():
    config   = ConstellationConfig.from_yaml(SCENARIO_FILES[1])
    manager  = ConstellationManager(config, time_multiplier=60.0)
    assert manager.time_multiplier == 60.0
    snap1 = manager.generate_all()
    snap2 = manager.generate_all()
    # Simulated timestamps should advance by 60 seconds per tick
    t1 = list(snap1.values())[0].timestamp
    t2 = list(snap2.values())[0].timestamp
    delta = (t2 - t1).total_seconds()
    assert abs(delta - 60.0) < 1.0

def test_manager_repr_contains_scenario():
    config  = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    manager = ConstellationManager(config, time_multiplier=10.0)
    r = repr(manager)
    assert "scenario=3" in r
    assert "satellites=24" in r
    assert "10.0" in r


# ── S7-16: Scenario 4 — hybrid 6 HEO (1 per plane) + 12 LEO (2 planes x 6) ────

def _heo(config):
    return [s for s in config.satellites if s.orbit_type == "HEO_MOLNIYA"]

def _leo(config):
    return [s for s in config.satellites if s.orbit_type == "LEO_CIRCULAR"]

def test_scenario_4_heo_six_planes_one_sat_each():
    heo = _heo(ConstellationConfig.from_yaml(SCENARIO_FILES[4]))
    assert {s.plane_id for s in heo} == {"MA", "MB", "MC", "MD", "ME", "MF"}
    assert len(heo) == 6

def test_scenario_4_heo_raan_spacing_60():
    heo = _heo(ConstellationConfig.from_yaml(SCENARIO_FILES[4]))
    assert sorted(s.raan_deg for s in heo) == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]

def test_scenario_4_heo_mean_anomaly_phased_per_plane():
    """With 1 sat per plane the per-plane offset is the only phasing source."""
    heo = _heo(ConstellationConfig.from_yaml(SCENARIO_FILES[4]))
    assert sorted(s.mean_anomaly_deg for s in heo) == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]

def test_scenario_4_heo_carries_molniya_elements():
    for s in _heo(ConstellationConfig.from_yaml(SCENARIO_FILES[4])):
        assert s.inclination_deg == 63.4
        assert s.arg_perigee_deg == 270.0
        assert s.apogee_altitude_km == 39750.0
        assert s.altitude_km == 500.0

def test_scenario_4_leo_shell_is_two_planes_of_six():
    leo = _leo(ConstellationConfig.from_yaml(SCENARIO_FILES[4]))
    assert {s.plane_id for s in leo} == {"LA", "LB"}
    assert sorted({s.raan_deg for s in leo}) == [0.0, 90.0]
    assert all(s.altitude_km == 550.0 and s.inclination_deg == 53.0 for s in leo)
    assert len(leo) == 12

def test_leo_scenarios_unchanged_by_heo_fields():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[3])
    for s in config.satellites:
        assert s.apogee_altitude_km is None
        assert s.arg_perigee_deg == 90.0

def test_scenario_4_orbit_classes_mapping():
    classes = ConstellationConfig.from_yaml(SCENARIO_FILES[4]).orbit_classes()
    assert sum(v == "HEO" for v in classes.values()) == 6
    assert sum(v == "LEO" for v in classes.values()) == 12

def test_scenario_4_satellite_ids_unique_and_distinct_from_scenario_3():
    s4 = [s.satellite_id for s in ConstellationConfig.from_yaml(SCENARIO_FILES[4]).satellites]
    s3 = {s.satellite_id for s in ConstellationConfig.from_yaml(SCENARIO_FILES[3]).satellites}
    assert len(s4) == len(set(s4)) == 18
    assert not (set(s4) & s3)


# ── Anomaly-training data sources (ADR-016) ───────────────────────────────────

def test_scenario_4_supplies_both_training_orbit_types():
    """Both orbit classes of Scenario 4 train, each on its own orbit_type."""
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    assert {s.orbit_type for s in config.satellites} == {"HEO_MOLNIYA", "LEO_CIRCULAR"}

def test_scenario_4_telemetry_separable_by_orbit_type():
    """Every record carries its satellite's orbit_type, so a query on orbit_type
    returns the HEO satellites only or the LEO satellites only, never a mixture."""
    config    = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    snapshots = ConstellationManager(config).generate_all()
    heo = {k for k, t in snapshots.items() if t.orbit_type == "HEO_MOLNIYA"}
    leo = {k for k, t in snapshots.items() if t.orbit_type == "LEO_CIRCULAR"}
    assert heo == {s.satellite_id for s in _heo(config)}
    assert leo == {s.satellite_id for s in _leo(config)}
    assert not (heo & leo)

def test_training_query_selects_by_orbit_type_only():
    """train.py separates the models by orbit_type; no per-satellite exclusion remains."""
    import pathlib
    src = pathlib.Path("services/anomaly_detection/train.py").read_text(encoding="utf-8")
    assert "WHERE orbit_type = %s" in src
    assert "satellite_id <> ALL" not in src
