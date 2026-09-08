"""
Tests for BatteryModel — eclipse-aware discharge/charge physics.
"""
import pytest
from services.satellite_simulator.battery import BatteryModel


@pytest.fixture
def battery():
    return BatteryModel(initial_pct=50.0)


# ── Return shape ──────────────────────────────────────────────────────────────

def test_update_returns_three_values(battery):
    result = battery.update(in_eclipse=False)
    assert len(result) == 3


# ── Discharge / charge direction ──────────────────────────────────────────────

def test_battery_discharges_in_eclipse():
    """Net discharge over 100 eclipse ticks from 50 %."""
    b = BatteryModel(initial_pct=50.0)
    for _ in range(100):
        b.update(in_eclipse=True)
    assert b.battery_pct < 50.0, "Battery should discharge in eclipse"


def test_battery_charges_in_sunlight():
    """Net charge over 100 sunlight ticks from 50 %."""
    b = BatteryModel(initial_pct=50.0)
    for _ in range(100):
        b.update(in_eclipse=False)
    assert b.battery_pct > 50.0, "Battery should charge in sunlight"


# ── Boundary clamping ─────────────────────────────────────────────────────────

def test_battery_never_below_zero():
    b = BatteryModel(initial_pct=0.0)
    for _ in range(500):
        b.update(in_eclipse=True)
    assert b.battery_pct >= 0.0


def test_battery_never_above_100():
    b = BatteryModel(initial_pct=100.0)
    for _ in range(500):
        b.update(in_eclipse=False)
    assert b.battery_pct <= 100.0


# ── Voltage consistency ───────────────────────────────────────────────────────

def test_voltage_increases_with_soc():
    low  = BatteryModel(initial_pct=10.0)
    high = BatteryModel(initial_pct=90.0)
    # Force one update without eclipse to keep SoC roughly stable
    _, v_low,  _ = low.update(in_eclipse=False)
    _, v_high, _ = high.update(in_eclipse=False)
    assert v_high > v_low, "Higher SoC should give higher terminal voltage"


def test_voltage_in_physical_range(battery):
    for _ in range(10):
        _, v, _ = battery.update(in_eclipse=False)
        assert 25.0 <= v <= 29.0, f"Voltage {v:.3f} V outside plausible range"


# ── Solar panel model ─────────────────────────────────────────────────────────

def test_solar_power_is_zero_in_eclipse(battery):
    _, _, solar = battery.update(in_eclipse=True)
    assert solar == 0.0, "Solar panel power must be 0 W in eclipse"


def test_solar_power_positive_in_sunlight(battery):
    _, _, solar = battery.update(in_eclipse=False)
    assert solar > 0.0, "Solar panel power must be positive in sunlight"


def test_solar_power_near_nominal_in_sunlight():
    """Solar output should cluster around 1300 W over many samples."""
    b      = BatteryModel(initial_pct=50.0)
    values = [b.update(in_eclipse=False)[2] for _ in range(200)]
    mean   = sum(values) / len(values)
    assert 1200.0 <= mean <= 1400.0, f"Mean solar power {mean:.1f} W far from 1300 W nominal"
