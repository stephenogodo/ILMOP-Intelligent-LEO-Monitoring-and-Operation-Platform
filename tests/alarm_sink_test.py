"""
tests/alarm_sink_test.py

Tests for the AlarmSink service.
Mirrors the pattern of sink_test.py — no live Kafka or DB required.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from services.alarm_sink.sink import AlarmSink


@pytest.fixture
def sink(monkeypatch):
    """AlarmSink with DB and Kafka connections patched out."""
    monkeypatch.setattr(
        "services.alarm_sink.sink.psycopg2.connect",
        MagicMock()
    )
    monkeypatch.setattr(
        "services.alarm_sink.sink.Consumer",
        MagicMock()
    )
    return AlarmSink()


def _make_alarm_msg(**kwargs):
    """Build a mock Kafka message containing a valid Alarm JSON payload."""
    import json
    defaults = dict(
        satellite_id    = "SAT-A4",
        timestamp       = datetime.now(timezone.utc).isoformat(),
        orbit_type      = "LEO_CIRCULAR",
        severity        = "WARNING",
        alarm_type      = "ANOMALY_SCORE",
        parameter       = "anomaly_score",
        observed_value  = -0.08,
        expected_min    = -0.05,
        expected_max    = 1.0,
        anomaly_score   = -0.0848,
        model_version   = "latest",
        message         = "WARNING: anomaly_score=-0.08 on SAT-A4",
        from_fault_injection = False,
        schema_version  = "1.0",
    )
    defaults.update(kwargs)
    msg = MagicMock()
    msg.value.return_value = json.dumps(defaults).encode("utf-8")
    msg.error.return_value = None
    return msg


# ── Parse tests ───────────────────────────────────────────────────────────────

def test_parse_valid_alarm(sink):
    record = sink._parse(_make_alarm_msg())
    assert record is not None
    assert record["satellite_id"] == "SAT-A4"

def test_parse_returns_none_on_invalid_json(sink):
    msg = MagicMock()
    msg.value.return_value = b"not json"
    msg.error.return_value = None
    assert sink._parse(msg) is None

def test_parse_severity_preserved(sink):
    record = sink._parse(_make_alarm_msg(severity="CRITICAL"))
    assert record["severity"] == "CRITICAL"

def test_parse_orbit_type_preserved(sink):
    record = sink._parse(_make_alarm_msg(orbit_type="HEO_MOLNIYA"))
    assert record["orbit_type"] == "HEO_MOLNIYA"

def test_parse_fault_injection_flag_preserved(sink):
    record = sink._parse(_make_alarm_msg(from_fault_injection=True))
    assert record["from_fault_injection"] is True

def test_parse_anomaly_score_preserved(sink):
    record = sink._parse(_make_alarm_msg(anomaly_score=-0.1234))
    assert abs(record["anomaly_score"] - (-0.1234)) < 0.0001

def test_parse_all_required_fields_present(sink):
    record = sink._parse(_make_alarm_msg())
    required = {
        "timestamp", "satellite_id", "orbit_type",
        "severity", "alarm_type", "parameter",
        "observed_value", "expected_min", "expected_max",
        "anomaly_score", "model_version", "message",
        "from_fault_injection", "schema_version",
    }
    assert required.issubset(set(record.keys()))
