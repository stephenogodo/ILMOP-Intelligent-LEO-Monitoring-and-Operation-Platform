"""
tests/alarm_schema_test.py

Tests for the Alarm Pydantic schema.
"""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from shared.schemas.alarm_schema import Alarm


def _make_alarm(**kwargs):
    defaults = dict(
        satellite_id    = "SAT-001",
        timestamp       = datetime.now(timezone.utc),
        orbit_type      = "LEO_CIRCULAR",
        severity        = "WARNING",
        alarm_type      = "ANOMALY_SCORE",
        parameter       = "battery_pct",
        observed_value  = 12.5,
        expected_min    = 20.0,
        expected_max    = 100.0,
        anomaly_score   = -0.12,
        model_version   = "latest",
        message         = "Battery low",
    )
    defaults.update(kwargs)
    return Alarm(**defaults)


def test_alarm_creates_successfully():
    alarm = _make_alarm()
    assert alarm.satellite_id == "SAT-001"

def test_alarm_default_orbit_type():
    alarm = _make_alarm()
    assert alarm.orbit_type == "LEO_CIRCULAR"

def test_alarm_heo_molniya_orbit_type():
    alarm = _make_alarm(orbit_type="HEO_MOLNIYA")
    assert alarm.orbit_type == "HEO_MOLNIYA"

def test_alarm_invalid_orbit_type_rejected():
    with pytest.raises(ValidationError):
        _make_alarm(orbit_type="INVALID")

def test_alarm_valid_severities():
    for sev in ["INFO", "WARNING", "CRITICAL"]:
        alarm = _make_alarm(severity=sev)
        assert alarm.severity == sev

def test_alarm_invalid_severity_rejected():
    with pytest.raises(ValidationError):
        _make_alarm(severity="URGENT")

def test_alarm_default_fault_injected_is_false():
    alarm = _make_alarm()
    assert alarm.from_fault_injection is False

def test_alarm_fault_injection_flag():
    alarm = _make_alarm(from_fault_injection=True)
    assert alarm.from_fault_injection is True

def test_alarm_schema_version():
    alarm = _make_alarm()
    assert alarm.schema_version == "1.0"

def test_alarm_serialises_to_json():
    alarm  = _make_alarm()
    data   = alarm.model_dump(mode="json")
    assert data["satellite_id"] == "SAT-001"
    assert isinstance(data["timestamp"], str)
    assert data["severity"] == "WARNING"
