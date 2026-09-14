"""
Tests for services/telemetry_sink/sink.py — unit tests that do not
require a live Kafka broker or TimescaleDB instance.

The _parse() method is tested in isolation: it validates the Kafka
message decoding and Pydantic schema validation path without any
network dependencies.
"""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from services.telemetry_sink.sink import TelemetrySink


def _make_mock_msg(payload: dict | None = None, bad_bytes: bool = False):
    """Create a mock Kafka message with the given payload."""
    msg = MagicMock()
    msg.offset.return_value = 0
    if bad_bytes:
        msg.value.return_value = b"\xff\xfe invalid utf-8"
    else:
        msg.value.return_value = json.dumps(payload).encode("utf-8")
    return msg


def _valid_payload():
    return dict(
        satellite_id="SAT-001",
        timestamp=datetime.now(timezone.utc).isoformat(),
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
        safe_mode=False,
        anomaly_flag=False,
    )


@pytest.fixture
def sink(monkeypatch):
    """TelemetrySink with DB and Kafka connections patched out."""
    monkeypatch.setattr(
        "services.telemetry_sink.sink.psycopg2.connect",
        MagicMock()
    )
    monkeypatch.setattr(
        "services.telemetry_sink.sink.Consumer",
        MagicMock()
    )
    return TelemetrySink()


def test_parse_valid_message(sink):
    msg    = _make_mock_msg(_valid_payload())
    record = sink._parse(msg)
    assert record is not None
    assert record["satellite_id"] == "SAT-001"
    assert record["schema_version"] == "2.1"


def test_parse_returns_none_on_invalid_utf8(sink):
    msg    = _make_mock_msg(bad_bytes=True)
    record = sink._parse(msg)
    assert record is None


def test_parse_returns_none_on_missing_field(sink):
    payload = _valid_payload()
    del payload["battery_pct"]
    msg    = _make_mock_msg(payload)
    record = sink._parse(msg)
    assert record is None


def test_parse_returns_none_on_wrong_type(sink):
    payload = _valid_payload()
    payload["battery_pct"] = "not-a-number"
    msg    = _make_mock_msg(payload)
    record = sink._parse(msg)
    assert record is None


def test_parse_eclipse_flag_preserved(sink):
    payload = _valid_payload()
    payload["in_eclipse"]         = True
    payload["solar_panel_power_w"] = 0.0
    msg    = _make_mock_msg(payload)
    record = sink._parse(msg)
    assert record["in_eclipse"] is True
    assert record["solar_panel_power_w"] == 0.0


def test_parse_all_fields_present(sink):
    msg    = _make_mock_msg(_valid_payload())
    record = sink._parse(msg)
    expected_keys = {
        "time", "satellite_id",
        "latitude_deg", "longitude_deg", "altitude_km",
        "in_eclipse", "in_contact",
        "battery_pct", "battery_voltage_v", "solar_panel_power_w",
        "temperature_c",
        "cpu_utilization_pct", "memory_utilization_pct",
        "downlink_rate_mbps", "uplink_rate_mbps",
        "safe_mode", "anomaly_flag", "schema_version",
    }
    assert expected_keys.issubset(set(record.keys()))
