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
    4: "config/constellations/scenario_4_molniya_polar.yaml",
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
    assert config.satellite_count == 6
    assert config.orbit_type == "HEO_MOLNIYA"

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

def test_molniya_has_correct_orbit_type():
    config = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    for sat in config.satellites:
        assert sat.orbit_type == "HEO_MOLNIYA"


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

def test_manager_scenario_4_has_heo_molniya_orbit_type():
    config    = ConstellationConfig.from_yaml(SCENARIO_FILES[4])
    manager   = ConstellationManager(config)
    snapshots = manager.generate_all()
    for sat_id, telemetry in snapshots.items():
        assert telemetry.orbit_type == "HEO_MOLNIYA"

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
