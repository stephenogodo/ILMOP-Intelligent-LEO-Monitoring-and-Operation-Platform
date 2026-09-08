"""
Tests for the Telemetry Pydantic schema.
"""
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from shared.schemas.telemetry_schema import Telemetry


def _valid_payload(**overrides):
    base = dict(
        satellite_id="SAT-001",
        timestamp=datetime.now(timezone.utc),
        latitude_deg=12.5,
        longitude_deg=-7.2,
        altitude_km=550.0,
        in_eclipse=False,
        in_contact=False,
        battery_pct=92.0,
        battery_voltage_v=28.3,
        solar_panel_power_w=1300.0,
        temperature_c=18.4,
        cpu_utilization_pct=35.0,
        memory_utilization_pct=40.0,
        downlink_rate_mbps=0.0,
        uplink_rate_mbps=0.0,
    )
    base.update(overrides)
    return base


def test_valid_payload_constructs():
    t = Telemetry(**_valid_payload())
    assert t.satellite_id == "SAT-001"


def test_safe_mode_defaults_to_false():
    t = Telemetry(**_valid_payload())
    assert t.safe_mode is False


def test_anomaly_flag_defaults_to_false():
    t = Telemetry(**_valid_payload())
    assert t.anomaly_flag is False


def test_missing_required_field_raises():
    payload = _valid_payload()
    del payload["satellite_id"]
    with pytest.raises(ValidationError):
        Telemetry(**payload)


def test_in_eclipse_is_bool():
    t = Telemetry(**_valid_payload(in_eclipse=True))
    assert t.in_eclipse is True


def test_in_contact_is_bool():
    t = Telemetry(**_valid_payload(in_contact=True))
    assert t.in_contact is True


def test_model_dump_json_mode():
    """JSON mode serialisation must work for Kafka transport."""
    t      = Telemetry(**_valid_payload())
    record = t.model_dump(mode="json")
    assert isinstance(record["timestamp"], str)   # datetime serialised as ISO string
    assert record["in_eclipse"] is False
