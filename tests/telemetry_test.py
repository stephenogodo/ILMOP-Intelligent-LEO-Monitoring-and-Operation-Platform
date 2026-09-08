"""
Tests for TelemetryGenerator — integration across all sub-models.
"""
import pytest
from services.satellite_simulator.telemetry import TelemetryGenerator
from shared.schemas.telemetry_schema import Telemetry


@pytest.fixture
def gen():
    return TelemetryGenerator(satellite_id="SAT-TEST")


# ── Output type & shape ───────────────────────────────────────────────────────

def test_generate_returns_telemetry(gen):
    assert isinstance(gen.generate(), Telemetry)


def test_satellite_id_correct(gen):
    assert gen.generate().satellite_id == "SAT-TEST"


def test_all_fields_present(gen):
    t = gen.generate()
    required = {
        "satellite_id", "timestamp",
        "latitude_deg", "longitude_deg", "altitude_km",
        "in_eclipse", "in_contact",
        "battery_pct", "battery_voltage_v", "solar_panel_power_w",
        "temperature_c",
        "cpu_utilization_pct", "memory_utilization_pct",
        "downlink_rate_mbps", "uplink_rate_mbps",
        "safe_mode", "anomaly_flag",
    }
    assert required.issubset(set(Telemetry.model_fields.keys()))


# ── Physical plausibility across 20 consecutive ticks ────────────────────────

@pytest.fixture
def twenty_samples():
    g = TelemetryGenerator("SAT-PHY")
    return [g.generate() for _ in range(20)]


def test_altitude_in_range(twenty_samples):
    for t in twenty_samples:
        assert 500.0 <= t.altitude_km <= 600.0


def test_latitude_bounded_by_inclination(twenty_samples):
    for t in twenty_samples:
        assert -52.0 <= t.latitude_deg <= 52.0


def test_battery_pct_bounded(twenty_samples):
    for t in twenty_samples:
        assert 0.0 <= t.battery_pct <= 100.0


def test_temperature_in_survival_range(twenty_samples):
    for t in twenty_samples:
        assert -30.0 <= t.temperature_c <= 70.0


def test_eclipse_consistent_with_solar_power(twenty_samples):
    """Solar panel power must be 0 whenever the satellite is in eclipse."""
    for t in twenty_samples:
        if t.in_eclipse:
            assert t.solar_panel_power_w == 0.0, (
                "Solar power must be 0 W during eclipse"
            )
        else:
            assert t.solar_panel_power_w > 0.0, (
                "Solar power must be positive when not in eclipse"
            )


def test_contact_consistent_with_link_rates(twenty_samples):
    """
    Link rates must be 0 when not in contact.
    (During contact rates may legitimately be 0 in the first tick due to
    Gaussian noise clipping — we only check the out-of-contact direction.)
    """
    for t in twenty_samples:
        if not t.in_contact:
            assert t.downlink_rate_mbps == 0.0
            assert t.uplink_rate_mbps   == 0.0


def test_cpu_in_range(twenty_samples):
    for t in twenty_samples:
        assert 5.0 <= t.cpu_utilization_pct <= 95.0


def test_serialisable_to_json(gen):
    """model_dump(mode='json') must succeed — required for Kafka JSON serialisation."""
    record = gen.generate().model_dump(mode="json")
    assert isinstance(record, dict)
    assert "satellite_id" in record
    assert "in_eclipse" in record
    assert "solar_panel_power_w" in record
