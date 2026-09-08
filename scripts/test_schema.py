from datetime import datetime
from shared.schemas.telemetry_schema import Telemetry

telemetry = Telemetry(
    satellite_id="SAT-001",
    timestamp=datetime.now(),
    latitude_deg=12.5,
    longitude_deg=-7.2,
    altitude_km=550.0,
    battery_pct=92.0,
    battery_voltage_v=28.3,
    solar_panel_power_w=1300,
    temperature_c=18.4,
    cpu_utilization_pct=35,
    memory_utilization_pct=40,
    downlink_rate_mbps=120,
    uplink_rate_mbps=20,
)

print(telemetry.model_dump())