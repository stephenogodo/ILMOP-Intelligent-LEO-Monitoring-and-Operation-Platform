"""
tests/api_test.py

FastAPI endpoint tests using Starlette's TestClient with mocked
database and cache dependencies.

No live Kafka, TimescaleDB, or Redis connection is required.
The database pool and Redis client are replaced with mocks via
FastAPI's dependency_overrides mechanism, keeping every test fast
and fully self-contained.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from services.api.db    import get_pool
from services.api.cache import get_cache


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _telemetry_record(satellite_id="SAT-001"):
    """Return a dict that mimics an asyncpg Record for a telemetry row."""
    return {
        "time":                   datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc),
        "satellite_id":           satellite_id,
        "latitude_deg":           12.5,
        "longitude_deg":          -7.2,
        "altitude_km":            550.0,
        "in_eclipse":             False,
        "in_contact":             False,
        "battery_pct":            92.0,
        "battery_voltage_v":      28.3,
        "solar_panel_power_w":    1300.0,
        "temperature_c":          18.4,
        "cpu_utilization_pct":    35.0,
        "memory_utilization_pct": 40.0,
        "downlink_rate_mbps":     0.0,
        "uplink_rate_mbps":       0.0,
        "safe_mode":              False,
        "anomaly_flag":           False,
    }


def _make_pool(fetchrow_return=None, fetch_return=None, fetchval_return=1):
    """Build a mock asyncpg pool with configurable return values."""
    pool = MagicMock()
    pool.fetchrow  = AsyncMock(return_value=fetchrow_return)
    pool.fetch     = AsyncMock(return_value=fetch_return or [])
    pool.fetchval  = AsyncMock(return_value=fetchval_return)
    return pool


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client_no_data():
    """API client where the DB returns no rows (empty fleet)."""
    mock_pool = _make_pool(fetchrow_return=None, fetch_return=[])
    app.dependency_overrides[get_pool]  = lambda: mock_pool
    app.dependency_overrides[get_cache] = lambda: None
    app.state.pool  = mock_pool
    app.state.redis = None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_data():
    """API client where the DB returns one telemetry record."""
    record    = _telemetry_record()
    mock_pool = _make_pool(fetchrow_return=record, fetch_return=[record])
    app.dependency_overrides[get_pool]  = lambda: mock_pool
    app.dependency_overrides[get_cache] = lambda: None
    app.state.pool  = mock_pool
    app.state.redis = None
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Health endpoint ───────────────────────────────────────────────────────────

def test_health_returns_200(client_with_data):
    r = client_with_data.get("/health")
    assert r.status_code == 200


def test_health_contains_status(client_with_data):
    body = client_with_data.get("/health").json()
    assert "status" in body
    assert "database" in body
    assert "timestamp" in body
    assert "version" in body


def test_health_version_is_sprint4(client_with_data):
    body = client_with_data.get("/health").json()
    assert body["version"] == "0.4.0"


# ── Satellites endpoint ───────────────────────────────────────────────────────

def test_satellites_returns_200(client_no_data):
    r = client_no_data.get("/satellites")
    assert r.status_code == 200


def test_satellites_returns_list(client_no_data):
    body = client_no_data.get("/satellites").json()
    assert isinstance(body, list)


def test_satellites_with_data_returns_satellite(client_with_data):
    body = client_with_data.get("/satellites").json()
    assert len(body) == 1
    assert body[0]["satellite_id"] == "SAT-001"


# ── Telemetry latest endpoint ─────────────────────────────────────────────────

def test_latest_returns_200_when_data_exists(client_with_data):
    r = client_with_data.get("/telemetry/SAT-001/latest")
    assert r.status_code == 200


def test_latest_returns_404_when_no_data(client_no_data):
    r = client_no_data.get("/telemetry/SAT-999/latest")
    assert r.status_code == 404


def test_latest_has_correct_satellite_id(client_with_data):
    body = client_with_data.get("/telemetry/SAT-001/latest").json()
    assert body["satellite_id"] == "SAT-001"


def test_latest_has_timestamp_not_time(client_with_data):
    """DB column 'time' must be renamed to 'timestamp' in the response."""
    body = client_with_data.get("/telemetry/SAT-001/latest").json()
    assert "timestamp" in body
    assert "time" not in body


def test_latest_has_all_telemetry_fields(client_with_data):
    body = client_with_data.get("/telemetry/SAT-001/latest").json()
    expected = {
        "satellite_id", "timestamp",
        "latitude_deg", "longitude_deg", "altitude_km",
        "in_eclipse", "in_contact",
        "battery_pct", "battery_voltage_v", "solar_panel_power_w",
        "temperature_c", "cpu_utilization_pct", "memory_utilization_pct",
        "downlink_rate_mbps", "uplink_rate_mbps",
        "safe_mode", "anomaly_flag",
    }
    assert expected.issubset(set(body.keys()))


def test_latest_eclipse_flag_is_bool(client_with_data):
    body = client_with_data.get("/telemetry/SAT-001/latest").json()
    assert isinstance(body["in_eclipse"], bool)


# ── Telemetry history endpoint ────────────────────────────────────────────────

def test_history_returns_200_when_data_exists(client_with_data):
    r = client_with_data.get("/telemetry/SAT-001")
    assert r.status_code == 200


def test_history_returns_list(client_with_data):
    body = client_with_data.get("/telemetry/SAT-001").json()
    assert isinstance(body, list)


def test_history_returns_404_when_no_data(client_no_data):
    r = client_no_data.get("/telemetry/SAT-999")
    assert r.status_code == 404


def test_history_limit_param_accepted(client_with_data):
    r = client_with_data.get("/telemetry/SAT-001?limit=50")
    assert r.status_code == 200


def test_history_limit_above_max_rejected(client_with_data):
    r = client_with_data.get("/telemetry/SAT-001?limit=99999")
    assert r.status_code == 422   # FastAPI validation error


def test_history_time_range_params_accepted(client_with_data):
    r = client_with_data.get(
        "/telemetry/SAT-001"
        "?from=2026-08-25T00:00:00Z"
        "&to=2026-08-25T01:00:00Z"
    )
    assert r.status_code == 200


# ── Response models (OpenAPI schema) ──────────────────────────────────────────

def _response_schema_ref(spec, path):
    ok = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return ok.get("$ref") or ok.get("items", {}).get("$ref")


def test_openapi_declares_response_models(client_no_data):
    spec = client_no_data.get("/openapi.json").json()
    expected = {
        "/health":                           "HealthResponse",
        "/satellites":                       "SatelliteStatus",
        "/telemetry/{satellite_id}/latest":  "TelemetryRecord",
        "/telemetry/{satellite_id}":         "TelemetryRecord",
        "/telemetry/{satellite_id}/summary": "TelemetrySummaryBucket",
        "/alarms/{satellite_id}":            "AlarmRecord",
    }
    for path, model in expected.items():
        ref = _response_schema_ref(spec, path)
        assert ref is not None and ref.endswith("/" + model), path


def test_satellites_full_row_keeps_all_fields(client_no_data):
    row = {
        "satellite_id": "SAT-001",
        "last_seen": datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc),
        "battery_pct": 90.0, "temperature_c": 20.0, "altitude_km": 550.0,
        "in_eclipse": False, "in_contact": True,
        "safe_mode": False, "anomaly_flag": False,
    }
    app.state.pool.fetch = AsyncMock(return_value=[row])
    body = client_no_data.get("/satellites").json()
    assert body[0]["battery_pct"] == 90.0
    assert body[0]["in_contact"] is True
    assert body[0]["last_seen"].startswith("2026-08-25T12:00:00")


def test_alarms_row_passes_through(client_no_data):
    row = {
        "satellite_id": "SAT-001",
        "timestamp": datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc),
        "orbit_type": "LEO_CIRCULAR", "severity": "WARNING",
        "alarm_type": "BATTERY", "parameter": "battery_pct",
        "observed_value": 12.0, "expected_min": 40.0, "expected_max": 100.0,
        "anomaly_score": -0.2, "model_version": "v7", "message": "low",
        "from_fault_injection": False,
    }
    app.state.pool.fetch = AsyncMock(return_value=[row])
    body = client_no_data.get("/alarms/SAT-001").json()
    assert body[0]["severity"] == "WARNING"
    assert body[0]["observed_value"] == 12.0
    assert isinstance(body[0]["timestamp"], str)


def test_summary_row_renames_time_to_timestamp(client_no_data):
    row = {
        "time": datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc),
        "satellite_id": "SAT-001",
        "avg_battery_pct": 91.0, "avg_battery_voltage_v": 28.0,
        "avg_solar_power_w": 1200.0, "avg_temperature_c": 19.0,
        "avg_cpu_pct": 30.0, "avg_memory_pct": 40.0, "avg_altitude_km": 550.0,
        "any_eclipse": False, "any_contact": False, "sample_count": 10,
    }
    app.state.pool.fetch = AsyncMock(return_value=[row])
    body = client_no_data.get("/telemetry/SAT-001/summary").json()
    assert body[0]["timestamp"].startswith("2026-08-25T12:00:00")
    assert body[0]["sample_count"] == 10
    assert "time" not in body[0]
