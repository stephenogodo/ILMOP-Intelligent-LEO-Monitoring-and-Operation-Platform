from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class Telemetry(BaseModel):
    """
    Immutable snapshot of all satellite telemetry parameters at a given instant.
    Produced by TelemetryGenerator and consumed by the Kafka pipeline, TimescaleDB
    sink, and operator dashboard.

    Schema version: 2.1  (adds orbit_type field to enforce LEO/HEO ML model
    separation — see ADR-016. Backward compatible: default value ensures all
    existing LEO records are correctly classified.)

    orbit_type values:
        "LEO_CIRCULAR" — Scenarios 1, 2, 3 (circular LEO orbits, 550 km)
        "HEO_MOLNIYA"  — Scenario 4 (Molniya HEO, 63.4°, e=0.74)
    """

    satellite_id:           str
    timestamp:              datetime

    # Orbital state
    latitude_deg:           float
    longitude_deg:          float
    altitude_km:            float
    in_eclipse:             bool
    in_contact:             bool

    # Orbit classification — gates ML model routing and training data separation
    orbit_type: Literal["LEO_CIRCULAR", "HEO_MOLNIYA"] = "LEO_CIRCULAR"

    # Power subsystem
    battery_pct:            float
    battery_voltage_v:      float
    solar_panel_power_w:    float

    # Thermal subsystem
    temperature_c:          float

    # Compute subsystem
    cpu_utilization_pct:    float
    memory_utilization_pct: float

    # Communications
    downlink_rate_mbps:     float
    uplink_rate_mbps:       float

    # Status flags
    safe_mode:              bool = False
    anomaly_flag:           bool = False
    fault_injected:         bool = False   # True when fault injection is active
