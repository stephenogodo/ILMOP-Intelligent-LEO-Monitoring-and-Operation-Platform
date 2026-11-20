"""
services/api/models.py

Pydantic response models for the ILMOP Telemetry API.

They describe what each endpoint already returns, so that /docs and
/openapi.json show real response schemas. Fields the database may leave
empty are optional.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status:    str = Field(description="'ok' when the database answers, otherwise 'degraded'")
    database:  str = Field(description="'connected' or 'unavailable'")
    timestamp: str = Field(description="Current UTC time, ISO 8601")
    version:   str = Field(description="API version")


class SatelliteStatus(BaseModel):
    satellite_id:  str
    last_seen:     Optional[datetime] = Field(default=None, description="Time of the latest record (UTC)")
    battery_pct:   Optional[float] = None
    temperature_c: Optional[float] = None
    altitude_km:   Optional[float] = None
    in_eclipse:    Optional[bool] = None
    in_contact:    Optional[bool] = None
    safe_mode:     Optional[bool] = None
    anomaly_flag:  Optional[bool] = None


class TelemetryRecord(BaseModel):
    satellite_id:           str
    timestamp:              str = Field(description="Record time (UTC, ISO 8601)")
    latitude_deg:           Optional[float] = None
    longitude_deg:          Optional[float] = None
    altitude_km:            Optional[float] = None
    in_eclipse:             Optional[bool] = None
    in_contact:             Optional[bool] = None
    battery_pct:            Optional[float] = None
    battery_voltage_v:      Optional[float] = None
    solar_panel_power_w:    Optional[float] = None
    temperature_c:          Optional[float] = None
    cpu_utilization_pct:    Optional[float] = None
    memory_utilization_pct: Optional[float] = None
    downlink_rate_mbps:     Optional[float] = None
    uplink_rate_mbps:       Optional[float] = None
    safe_mode:              Optional[bool] = None
    anomaly_flag:           Optional[bool] = None


class TelemetrySummaryBucket(BaseModel):
    satellite_id:          str
    timestamp:             str = Field(description="Start of the 5-minute bucket (UTC, ISO 8601)")
    avg_battery_pct:       Optional[float] = None
    avg_battery_voltage_v: Optional[float] = None
    avg_solar_power_w:     Optional[float] = None
    avg_temperature_c:     Optional[float] = None
    avg_cpu_pct:           Optional[float] = None
    avg_memory_pct:        Optional[float] = None
    avg_altitude_km:       Optional[float] = None
    any_eclipse:           Optional[bool] = None
    any_contact:           Optional[bool] = None
    sample_count:          Optional[int] = None


class AlarmRecord(BaseModel):
    satellite_id:         str
    timestamp:            str = Field(description="Alarm time (UTC, ISO 8601)")
    orbit_type:           Optional[str] = None
    severity:             str = Field(description="INFO, WARNING or CRITICAL")
    alarm_type:           Optional[str] = None
    parameter:            Optional[str] = None
    observed_value:       Optional[float] = None
    expected_min:         Optional[float] = None
    expected_max:         Optional[float] = None
    anomaly_score:        Optional[float] = None
    model_version:        Optional[str] = None
    message:              Optional[str] = None
    from_fault_injection: Optional[bool] = None
