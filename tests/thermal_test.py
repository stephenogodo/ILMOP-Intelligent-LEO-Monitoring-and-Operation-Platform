"""
Tests for ThermalModel — eclipse-aware thermal cycling.
"""
import pytest
from services.satellite_simulator.thermal import ThermalModel


@pytest.fixture
def thermal():
    return ThermalModel(initial_temp_c=20.0)


# ── Return type ───────────────────────────────────────────────────────────────

def test_update_returns_float(thermal):
    result = thermal.update(in_eclipse=False)
    assert isinstance(result, float)


# ── Thermal direction ─────────────────────────────────────────────────────────

def test_cools_toward_eclipse_target():
    """Starting warm, 300 eclipse ticks should move temperature well below 0 °C."""
    t = ThermalModel(initial_temp_c=30.0)
    for _ in range(300):
        t.update(in_eclipse=True)
    assert t.temperature_c < 0.0, (
        f"Temperature {t.temperature_c:.2f} °C should be well below 0 after 300 eclipse ticks"
    )


def test_warms_toward_sunlight_target():
    """Starting cold, 300 sunlight ticks should move temperature above 0 °C."""
    t = ThermalModel(initial_temp_c=-15.0)
    for _ in range(300):
        t.update(in_eclipse=False)
    assert t.temperature_c > 0.0, (
        f"Temperature {t.temperature_c:.2f} °C should be above 0 after 300 sunlight ticks"
    )


def test_eclipse_target_lower_than_sunlight():
    """Eclipse equilibrium must be colder than sunlight equilibrium."""
    t_ecl = ThermalModel(initial_temp_c=0.0)
    t_sun = ThermalModel(initial_temp_c=0.0)
    for _ in range(1000):
        t_ecl.update(in_eclipse=True)
        t_sun.update(in_eclipse=False)
    assert t_ecl.temperature_c < t_sun.temperature_c


# ── Hard limits ───────────────────────────────────────────────────────────────

def test_temperature_never_below_hard_minimum():
    t = ThermalModel(initial_temp_c=-25.0)
    for _ in range(5000):
        t.update(in_eclipse=True)
    assert t.temperature_c >= ThermalModel.TEMP_MIN_C


def test_temperature_never_above_hard_maximum():
    t = ThermalModel(initial_temp_c=65.0)
    for _ in range(5000):
        t.update(in_eclipse=False)
    assert t.temperature_c <= ThermalModel.TEMP_MAX_C


# ── Full orbit thermal swing ──────────────────────────────────────────────────

def test_thermal_swing_over_orbit():
    """
    Over a realistic eclipse / sunlight cycle the temperature range should
    span at least 10 °C (true LEO swing is typically 35–55 °C).
    """
    from services.satellite_simulator.orbit import OrbitModel
    from datetime import datetime, timedelta, timezone

    orbit  = OrbitModel()
    base   = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    t_model = ThermalModel(initial_temp_c=20.0)
    temps  = []

    for i in range(96):           # 96 × 1 min = one orbit
        t_time = base + timedelta(minutes=i)
        *_, in_ecl = orbit._propagate_at(t_time)
        # Advance the thermal model 60 ticks (= 60 s) per orbit sample,
        # matching the 1-second tick cadence the model is designed for.
        for _ in range(60):
            temp = t_model.update(in_eclipse=in_ecl)
        temps.append(temp)

    swing = max(temps) - min(temps)
    assert swing >= 10.0, f"Thermal swing {swing:.1f} °C is unrealistically small"
