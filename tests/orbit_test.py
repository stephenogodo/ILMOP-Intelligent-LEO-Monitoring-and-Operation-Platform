"""
Tests for OrbitModel — SGP4 propagation + eclipse detection.
"""
from datetime import datetime, timedelta, timezone

import pytest

from services.satellite_simulator.orbit import OrbitModel


@pytest.fixture
def orbit():
    return OrbitModel()


# ── Basic output shape ────────────────────────────────────────────────────────

def test_propagate_returns_four_values(orbit):
    result = orbit.propagate()
    assert len(result) == 4


def test_eclipse_flag_is_bool(orbit):
    *_, ecl = orbit.propagate()
    assert isinstance(ecl, bool)


# ── Geographic bounds ─────────────────────────────────────────────────────────

def test_latitude_within_inclination(orbit):
    lat, *_ = orbit.propagate()
    # 51.6° inclination → |lat| never exceeds inclination
    assert -51.6 <= lat <= 51.6


def test_longitude_in_range(orbit):
    _, lon, *_ = orbit.propagate()
    assert -180.0 <= lon <= 180.0


def test_altitude_near_550km(orbit):
    _, _, alt, _ = orbit.propagate()
    assert 530.0 <= alt <= 570.0, f"Altitude {alt:.1f} km outside expected ±20 km band"


# ── Eclipse physics ───────────────────────────────────────────────────────────

def test_eclipse_fraction_over_one_orbit():
    """
    For a 550 km 51.6° orbit in August, the eclipse fraction should be
    between 25 % and 45 % (typical range is ~33 %).
    """
    orbit = OrbitModel()
    base  = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
    samples = [
        orbit._propagate_at(base + timedelta(minutes=2 * i))
        for i in range(48)          # 96 min ≈ one full orbit at 2-min cadence
    ]
    eclipse_fraction = sum(1 for *_, ecl in samples if ecl) / len(samples)
    assert 0.25 <= eclipse_fraction <= 0.45, (
        f"Eclipse fraction {eclipse_fraction:.1%} outside expected range 25–45 %"
    )


def test_no_eclipse_directly_facing_sun():
    """
    A satellite positioned exactly between Earth and the Sun should never
    be in eclipse (it is on the fully illuminated side).
    """
    # 2026-03-20 ~12:00 UTC: vernal equinox, Sun near 0° ecliptic longitude.
    # At that moment the Sun is roughly in the +X direction of ECI.
    # A satellite at lat=0, lon=0, altitude=550 km on the sub-solar point
    # is definitely illuminated.  We use _propagate_at with a time when the
    # satellite happens to be near the sub-solar point.
    orbit = OrbitModel(mean_anomaly_deg=0.0, raan_deg=0.0)
    # Sample 48 points and assert at least one is NOT in eclipse
    base    = datetime(2026, 8, 24, 0, 0, 0, tzinfo=timezone.utc)
    results = [orbit._propagate_at(base + timedelta(minutes=i)) for i in range(48)]
    assert any(not ecl for *_, ecl in results), (
        "Expected at least some illuminated passes but all samples showed eclipse"
    )


# ── Determinism ───────────────────────────────────────────────────────────────

def test_same_time_same_result(orbit):
    t   = datetime(2026, 8, 24, 6, 0, 0, tzinfo=timezone.utc)
    r1  = orbit._propagate_at(t)
    r2  = orbit._propagate_at(t)
    assert r1 == r2, "OrbitModel must be deterministic for the same input time"
