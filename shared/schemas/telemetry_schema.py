from datetime import datetime
from pydantic import BaseModel


class Telemetry(BaseModel):
    """
    Immutable snapshot of all satellite telemetry parameters at a given instant.
    Produced by TelemetryGenerator and consumed by the Kafka pipeline, TimescaleDB
    sink, and operator dashboard.

    Schema version: 2.0  (adds in_eclipse, in_contact; all six previously-static
    fields are now dynamic; safe for Kafka JSON serialisation via model_dump(mode='json'))
    """

    satellite_id:           str
    timestamp:              datetime

    # Orbital state
    latitude_deg:           float
    longitude_deg:          float
    altitude_km:            float
    in_eclipse:             bool
    in_contact:             bool

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
    safe_mode:    bool = False
    anomaly_flag: bool = False
