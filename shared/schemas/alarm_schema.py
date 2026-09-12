"""
shared/schemas/alarm_schema.py

Pydantic v2 schema for the Alarm data contract.

Alarms are produced by the anomaly detection service and published to
the alarms.{satellite_id} Kafka topic.  They are consumed by the
dashboard alarm panel and the FastAPI alarms endpoint.

Severity levels:
    INFO     — statistically unusual but within physically plausible bounds
    WARNING  — clear deviation from normal; early-stage degradation likely
    CRITICAL — severe deviation; imminent subsystem failure or safe mode
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Alarm(BaseModel):
    """
    Immutable alarm record produced by the anomaly detection service.

    Schema version: 1.0
    """

    satellite_id: str
    timestamp:    datetime

    # Orbit classification — matches the Telemetry orbit_type that triggered this alarm
    orbit_type: Literal["LEO_CIRCULAR", "HEO_MOLNIYA"] = "LEO_CIRCULAR"

    # Alarm classification
    severity:   Literal["INFO", "WARNING", "CRITICAL"]
    alarm_type: str    # e.g. "ANOMALY_SCORE", "BATTERY_LOW", "THERMAL_HIGH"

    # Triggering parameter and values
    parameter:       str    # e.g. "battery_pct", "temperature_c"
    observed_value:  float
    expected_min:    float
    expected_max:    float

    # Anomaly detection model output
    anomaly_score:   float   # Isolation Forest decision function score
    model_version:   str     # MLflow model version that produced this alarm

    # Human-readable description
    message: str

    # Whether the telemetry record had fault_injected=True
    # (alarms from injected faults should not trigger operator responses)
    from_fault_injection: bool = False

    schema_version: str = "1.0"
