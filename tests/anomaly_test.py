"""
tests/anomaly_test.py

Tests for the anomaly detection feature engineering and scoring logic.
"""

from datetime import datetime, timezone
import numpy as np
import pytest
from shared.schemas.telemetry_schema import Telemetry
from services.anomaly_detection.features import (
    extract_features,
    extract_features_batch,
    FEATURE_NAMES,
)
from services.anomaly_detection.detector import score_to_severity, build_alarm


def _make_telemetry(**kwargs):
    defaults = dict(
        satellite_id            = "SAT-001",
        timestamp               = datetime.now(timezone.utc),
        latitude_deg            = 12.5,
        longitude_deg           = -7.2,
        altitude_km             = 550.0,
        in_eclipse              = False,
        in_contact              = False,
        orbit_type              = "LEO_CIRCULAR",
        battery_pct             = 87.0,
        battery_voltage_v       = 28.0,
        solar_panel_power_w     = 1300.0,
        temperature_c           = 20.0,
        cpu_utilization_pct     = 35.0,
        memory_utilization_pct  = 40.0,
        downlink_rate_mbps      = 0.0,
        uplink_rate_mbps        = 0.0,
    )
    defaults.update(kwargs)
    return Telemetry(**defaults)


# ── Feature extraction tests ──────────────────────────────────────────────────

def test_feature_vector_has_correct_length():
    tel      = _make_telemetry()
    features = extract_features(tel)
    assert len(features) == len(FEATURE_NAMES)

def test_feature_vector_is_numpy_array():
    tel = _make_telemetry()
    features = extract_features(tel)
    assert isinstance(features, np.ndarray)

def test_eclipse_flag_encoded_as_int():
    tel_sun     = _make_telemetry(in_eclipse=False)
    tel_eclipse = _make_telemetry(in_eclipse=True)
    f_sun     = extract_features(tel_sun)
    f_eclipse = extract_features(tel_eclipse)
    eclipse_idx = FEATURE_NAMES.index("in_eclipse_int")
    assert f_sun[eclipse_idx]     == 0.0
    assert f_eclipse[eclipse_idx] == 1.0

def test_solar_zero_in_eclipse():
    tel      = _make_telemetry(in_eclipse=True, solar_panel_power_w=0.0)
    features = extract_features(tel)
    solar_idx   = FEATURE_NAMES.index("solar_panel_power_w")
    product_idx = FEATURE_NAMES.index("battery_solar_product")
    assert features[solar_idx]   == 0.0
    assert features[product_idx] == 0.0

def test_batch_extraction_shape():
    records = [_make_telemetry() for _ in range(10)]
    X = extract_features_batch(records)
    assert X.shape == (10, len(FEATURE_NAMES))


# ── Severity threshold tests ──────────────────────────────────────────────────

def test_normal_score_returns_none():
    assert score_to_severity(0.1)  is None
    assert score_to_severity(0.0)  is None
    assert score_to_severity(-0.04) is None

def test_warning_score():
    assert score_to_severity(-0.06) == "WARNING"
    assert score_to_severity(-0.10) == "WARNING"

def test_critical_score():
    assert score_to_severity(-0.16) == "CRITICAL"
    assert score_to_severity(-0.50) == "CRITICAL"


# ── Alarm building tests ──────────────────────────────────────────────────────

def test_build_alarm_returns_alarm_object():
    from shared.schemas.alarm_schema import Alarm
    tel   = _make_telemetry(battery_pct=12.0)
    alarm = build_alarm(tel, score=-0.20, severity="CRITICAL")
    assert isinstance(alarm, Alarm)
    assert alarm.severity == "CRITICAL"
    assert alarm.satellite_id == "SAT-001"

def test_build_alarm_battery_low_parameter():
    tel   = _make_telemetry(battery_pct=10.0)
    alarm = build_alarm(tel, score=-0.25, severity="CRITICAL")
    assert alarm.parameter == "battery_pct"
    assert alarm.observed_value == pytest.approx(10.0)

def test_build_alarm_fault_injection_flagged():
    tel   = _make_telemetry(fault_injected=True)
    alarm = build_alarm(tel, score=-0.20, severity="CRITICAL")
    assert alarm.from_fault_injection is True

def test_build_alarm_orbit_type_propagated():
    tel   = _make_telemetry(orbit_type="HEO_MOLNIYA")
    alarm = build_alarm(tel, score=-0.10, severity="WARNING")
    assert alarm.orbit_type == "HEO_MOLNIYA"
