from dataclasses import dataclass


@dataclass
class Satellite:
    """
    Stateful domain object representing a single satellite in the simulation.

    TelemetryGenerator holds one Satellite instance and mutates it every tick.
    At the end of each tick the current state is snapshotted into an immutable
    Telemetry object (the record that enters Kafka / TimescaleDB).

    Separation of concerns
    ──────────────────────
    Satellite  = mutable, in-memory state (what the satellite IS right now)
    Telemetry  = immutable snapshot      (what we RECORDED at a point in time)
    """

    satellite_id: str

    # ── Orbital state ─────────────────────────────────────────────────────────
    latitude_deg:  float = 0.0
    longitude_deg: float = 0.0
    altitude_km:   float = 550.0
    in_eclipse:    bool  = False
    in_contact:    bool  = False

    # ── Power subsystem ───────────────────────────────────────────────────────
    battery_pct:         float = 95.0
    battery_voltage_v:   float = 28.0
    solar_panel_power_w: float = 1300.0

    # ── Thermal subsystem ─────────────────────────────────────────────────────
    temperature_c: float = 20.0

    # ── Compute subsystem ─────────────────────────────────────────────────────
    cpu_utilization_pct:    float = 35.0
    memory_utilization_pct: float = 40.0

    # ── Communications ────────────────────────────────────────────────────────
    downlink_rate_mbps: float = 0.0
    uplink_rate_mbps:   float = 0.0

    # ── Status flags ──────────────────────────────────────────────────────────
    safe_mode:    bool = False
    anomaly_flag: bool = False
