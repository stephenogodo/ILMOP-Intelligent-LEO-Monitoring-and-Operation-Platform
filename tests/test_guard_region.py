"""
tests/test_guard_region.py

Test suite for waveform/guard_region.py.

Exit conditions (from Waveform Design Notes / WDR-006):
    1. FixedGuardRegion returns constant l_guard throughout pass.
    2. AdaptiveGuardRegion narrows to safety_bins at zenith (elevation=90°).
    3. AdaptiveGuardRegion equals FixedGuardRegion at horizon (elevation=0°).
    4. Pass-averaged adaptive efficiency > pass-averaged fixed efficiency.
    5. Both satisfy contamination probability bound (guard ≥ safety_bins).
"""

import math
import pytest

from waveform.otfs_signal import OTFSGrid
from waveform.guard_region import (
    AdaptiveGuardRegion,
    FixedGuardRegion,
    GuardResult,
    PassEfficiencyResult,
    make_sband_leo_guards,
    n_max_for_grid,
    nu_max_hz,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def leo_grid():
    """N=32, M=256 — correct LEO grid for S-band 10 MHz (WDR-005)."""
    return OTFSGrid(N=32, M=256, bandwidth_hz=10e6)


@pytest.fixture
def small_grid():
    """N=8, M=64 — small grid for fast algebraic tests."""
    return OTFSGrid(N=8, M=64, bandwidth_hz=10e6)


@pytest.fixture
def nu_max_sband(leo_grid):
    """ν_max for S-band (2.4 GHz) at 550 km."""
    return nu_max_hz(550.0, 2.4e9)


@pytest.fixture
def fixed(leo_grid, nu_max_sband):
    return FixedGuardRegion(leo_grid, nu_max_hz=nu_max_sband, k_guard_pos=10)


@pytest.fixture
def adaptive(leo_grid):
    return AdaptiveGuardRegion(leo_grid, carrier_freq_hz=2.4e9, altitude_km=550.0,
                               k_guard_pos=10)


@pytest.fixture
def elevation_pass():
    """Synthetic pass profile: horizon → zenith → horizon."""
    elevations = list(range(5, 90, 5)) + [90] + list(range(85, 4, -5))
    return elevations


# ══════════════════════════════════════════════════════════════════════════════
# 0 — Helper functions
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_nu_max_sband_550km(self):
        """ν_max at S-band (2.4 GHz), h=550 km ≈ 60.8 kHz."""
        nu = nu_max_hz(550.0, 2.4e9)
        assert 58e3 < nu < 63e3, f"Expected ~60.8 kHz, got {nu/1e3:.1f} kHz"

    def test_nu_max_scales_with_frequency(self):
        """ν_max doubles when carrier frequency doubles (same altitude)."""
        nu_s = nu_max_hz(550.0, 2.4e9)
        nu_c = nu_max_hz(550.0, 4.8e9)
        assert abs(nu_c / nu_s - 2.0) < 0.01

    def test_nu_max_nearly_independent_of_altitude(self):
        """ν_max varies < 7% from h=350 km to h=1200 km (WDR-005 Finding 4)."""
        nu_350  = nu_max_hz(350.0,  2.4e9)
        nu_1200 = nu_max_hz(1200.0, 2.4e9)
        variation = abs(nu_350 - nu_1200) / nu_350
        assert variation < 0.07, f"Altitude variation {variation:.1%} exceeds 7%"

    def test_n_max_sband_10mhz(self):
        """N_max at S-band, B=10 MHz ≤ 80 (WDR-005)."""
        n_max = n_max_for_grid(10e6, 550.0, 2.4e9)
        assert n_max <= 82   # ~80 depending on exact v_orb

    def test_n_max_increases_with_bandwidth(self):
        """N_max scales linearly with bandwidth (WDR-005)."""
        n10 = n_max_for_grid(10e6, 550.0, 2.4e9)
        n20 = n_max_for_grid(20e6, 550.0, 2.4e9)
        assert abs(n20 / n10 - 2.0) < 0.05

    def test_n_max_decreases_with_frequency(self):
        """N_max decreases at higher frequencies (WDR-005)."""
        n_s  = n_max_for_grid(10e6, 550.0, 2.4e9)
        n_ka = n_max_for_grid(10e6, 550.0, 30.0e9)
        assert n_ka < n_s
        assert n_ka <= 7   # Ka-band at 10 MHz: N_max ≈ 6

    def test_n_max_ka_band_impractical(self):
        """Ka-band at B=10 MHz gives N_max ≤ 6 (impractical, WDR-005 Finding 2)."""
        n_max = n_max_for_grid(10e6, 550.0, 30e9)
        assert n_max <= 7


# ══════════════════════════════════════════════════════════════════════════════
# 1 — FixedGuardRegion
# ══════════════════════════════════════════════════════════════════════════════

class TestFixedGuardRegion:

    def test_l_guard_is_constant(self, fixed, elevation_pass):
        """EXIT CONDITION 1: l_guard is the same at every elevation angle."""
        results = [fixed.compute(el) for el in elevation_pass]
        l_guards = [r.l_guard for r in results]
        assert len(set(l_guards)) == 1, \
            f"FixedGuardRegion l_guard varied: {set(l_guards)}"

    def test_l_guard_covers_nu_max(self, fixed, nu_max_sband, leo_grid):
        """l_guard × Δν ≥ ν_max (guard covers the full Doppler spread)."""
        result = fixed.compute()
        covered_hz = result.l_guard * leo_grid.doppler_resolution_hz
        assert covered_hz >= nu_max_sband, \
            f"Guard covers {covered_hz/1e3:.1f} kHz < ν_max={nu_max_sband/1e3:.1f} kHz"

    def test_data_fraction_positive(self, fixed):
        """Fixed guard leaves a positive data fraction."""
        assert fixed.data_fraction > 0.0

    def test_data_fraction_less_than_one(self, fixed):
        """Fixed guard consumes some of the frame."""
        assert fixed.data_fraction < 1.0

    def test_guard_fits_within_m(self, fixed, leo_grid):
        """Total Doppler guard width ≤ M."""
        assert 2 * fixed.l_guard + 1 <= leo_grid.M

    def test_data_fraction_constant(self, fixed, elevation_pass):
        """Data fraction is the same at every epoch for FixedGuardRegion."""
        results = fixed.compute_for_pass(elevation_pass)
        fracs = [r.data_fraction for r in results]
        assert max(fracs) - min(fracs) < 1e-9

    def test_rejects_oversized_guard(self, leo_grid):
        """FixedGuardRegion raises ValueError when guard exceeds M."""
        with pytest.raises(ValueError, match="WDR-005"):
            FixedGuardRegion(leo_grid, nu_max_hz=500e3)  # 500 kHz — too large

    def test_safety_bins_floor(self, leo_grid):
        """l_guard ≥ safety_bins even at zero Doppler."""
        guard = FixedGuardRegion(leo_grid, nu_max_hz=100.0, safety_bins=5)
        assert guard.l_guard >= 5

    def test_compute_for_pass_skips_below_horizon(self, fixed):
        """compute_for_pass skips epochs with elevation < 0°. Epochs at 0° are included."""
        profile = [-5.0, 0.0, 10.0, 45.0, 10.0, 0.0, -5.0]
        results = fixed.compute_for_pass(profile)
        # -5° skipped × 2; 0°, 10°, 45°, 10°, 0° included = 5
        assert len(results) == 5


# ══════════════════════════════════════════════════════════════════════════════
# 2 — AdaptiveGuardRegion
# ══════════════════════════════════════════════════════════════════════════════

class TestAdaptiveGuardRegion:

    def test_narrows_at_zenith(self, adaptive):
        """EXIT CONDITION 2: AdaptiveGuardRegion narrows to safety_bins at zenith."""
        result = adaptive.compute(elevation_deg=90.0)
        assert result.l_guard == adaptive.safety_bins, (
            f"At zenith: expected l_guard={adaptive.safety_bins} "
            f"(safety_bins), got {result.l_guard}"
        )

    def test_equals_fixed_at_horizon(self, adaptive, fixed):
        """EXIT CONDITION 3: AdaptiveGuardRegion equals FixedGuardRegion at horizon."""
        result_adaptive = adaptive.compute(elevation_deg=0.0)
        result_fixed    = fixed.compute(elevation_deg=0.0)
        assert result_adaptive.l_guard == result_fixed.l_guard, (
            f"At horizon: adaptive l_guard={result_adaptive.l_guard} "
            f"≠ fixed l_guard={result_fixed.l_guard}"
        )

    def test_guard_narrows_monotonically_with_elevation(self, adaptive):
        """l_guard decreases (or stays the same) as elevation increases."""
        elevations = [0, 10, 20, 30, 45, 60, 75, 90]
        results = [adaptive.compute(el) for el in elevations]
        l_guards = [r.l_guard for r in results]
        for i in range(len(l_guards) - 1):
            assert l_guards[i] >= l_guards[i+1], (
                f"Guard increased from el={elevations[i]}° to {elevations[i+1]}°: "
                f"{l_guards[i]} → {l_guards[i+1]}"
            )

    def test_adaptive_exceeds_fixed_efficiency_at_zenith(self, adaptive, fixed):
        """Adaptive data fraction > fixed data fraction at high elevation."""
        result_adaptive = adaptive.compute(elevation_deg=80.0)
        result_fixed    = fixed.compute(elevation_deg=80.0)
        assert result_adaptive.data_fraction > result_fixed.data_fraction

    def test_safety_floor_maintained(self, adaptive):
        """l_guard ≥ safety_bins at all elevations (contamination bound)."""
        for el in range(0, 91, 5):
            result = adaptive.compute(el)
            assert result.l_guard >= adaptive.safety_bins, (
                f"Safety floor violated at elevation={el}°: "
                f"l_guard={result.l_guard} < safety_bins={adaptive.safety_bins}"
            )

    def test_compute_from_range_rate_at_zero(self, adaptive):
        """Range rate 0 m/s → l_guard = safety_bins."""
        result = adaptive.compute_from_range_rate(range_rate_ms=0.0)
        assert result.l_guard == adaptive.safety_bins

    def test_compute_from_range_rate_at_max(self, adaptive, fixed):
        """Range rate = v_orb → l_guard matches fixed guard."""
        result = adaptive.compute_from_range_rate(
            range_rate_ms=adaptive.v_orb_ms
        )
        assert result.l_guard == fixed.l_guard

    def test_rejects_oversized_nu_max(self, leo_grid):
        """AdaptiveGuardRegion raises ValueError when ν_max exceeds grid capacity."""
        # Ka-band (30 GHz) at 550 km with N=32, M=256 grid will fail
        # because ν_max ≈ 759 kHz → l_guard ≈ 622 > 128
        with pytest.raises(ValueError):
            AdaptiveGuardRegion(leo_grid, carrier_freq_hz=30e9, altitude_km=550.0)

    def test_compute_for_pass_from_frames_in_view_only(self, adaptive):
        """compute_for_pass_from_frames processes only in_view=True frames."""
        from dataclasses import dataclass
        from datetime import datetime, timezone

        @dataclass
        class MockFrame:
            range_rate_ms: float
            elevation_deg: float
            in_view: bool

        frames = [
            MockFrame(range_rate_ms=7000.0, elevation_deg=2.0, in_view=False),
            MockFrame(range_rate_ms=5000.0, elevation_deg=20.0, in_view=True),
            MockFrame(range_rate_ms=1000.0, elevation_deg=60.0, in_view=True),
            MockFrame(range_rate_ms=0.0,    elevation_deg=90.0, in_view=True),
            MockFrame(range_rate_ms=5000.0, elevation_deg=20.0, in_view=True),
            MockFrame(range_rate_ms=7000.0, elevation_deg=2.0, in_view=False),
        ]
        results = adaptive.compute_for_pass_from_frames(frames)
        assert len(results) == 4  # only in_view=True frames


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Pass-averaged efficiency comparison
# ══════════════════════════════════════════════════════════════════════════════

class TestPassEfficiencyComparison:

    def test_adaptive_better_than_fixed_pass_average(self, adaptive, elevation_pass):
        """EXIT CONDITION 4: pass-averaged adaptive efficiency > fixed efficiency."""
        result = adaptive.compare_with_fixed(elevation_pass)
        assert result.adaptive_eta_mean > result.fixed_eta_mean, (
            f"Adaptive η={result.adaptive_eta_mean:.1%} not > "
            f"fixed η={result.fixed_eta_mean:.1%}"
        )

    def test_delta_eta_mean_positive(self, adaptive, elevation_pass):
        """Pass-averaged efficiency gain Δη > 0."""
        result = adaptive.compare_with_fixed(elevation_pass)
        assert result.delta_eta_mean > 0.0

    def test_delta_eta_max_at_zenith(self, adaptive):
        """Maximum efficiency gain occurs at or near zenith (90°)."""
        profile = list(range(5, 91, 5))  # rising pass to zenith
        result = adaptive.compare_with_fixed(profile)
        assert result.delta_eta_max > result.delta_eta_mean, \
            "Maximum gain should exceed mean gain for a rising pass"

    def test_epoch_count_matches_in_view(self, adaptive, elevation_pass):
        """n_epochs equals number of in-view points in elevation profile."""
        in_view = [el for el in elevation_pass if el >= 0]
        result  = adaptive.compare_with_fixed(elevation_pass)
        assert result.n_epochs == len(in_view)

    def test_summary_string_contains_key_metrics(self, adaptive, elevation_pass):
        """PassEfficiencyResult.summary() contains fixed and adaptive η."""
        result = adaptive.compare_with_fixed(elevation_pass)
        s = result.summary()
        assert 'fixed' in s.lower()
        assert 'adaptive' in s.lower()

    def test_safety_floor_exit_condition(self, adaptive, elevation_pass):
        """EXIT CONDITION 5: l_guard ≥ safety_bins at all epochs (contamination bound)."""
        result = adaptive.compare_with_fixed(elevation_pass)
        for r in result.epochs_adaptive:
            assert r.l_guard >= adaptive.safety_bins, (
                f"Contamination bound violated at el={r.elevation_deg}°: "
                f"l_guard={r.l_guard} < safety_bins={adaptive.safety_bins}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Factory helper
# ══════════════════════════════════════════════════════════════════════════════

class TestFactory:

    def test_make_sband_leo_guards_returns_pair(self, leo_grid):
        """make_sband_leo_guards returns (FixedGuardRegion, AdaptiveGuardRegion)."""
        fixed, adaptive = make_sband_leo_guards(leo_grid)
        assert isinstance(fixed,    FixedGuardRegion)
        assert isinstance(adaptive, AdaptiveGuardRegion)

    def test_fixed_and_adaptive_same_nu_max(self, leo_grid):
        """Factory pair uses the same ν_max."""
        fixed, adaptive = make_sband_leo_guards(leo_grid)
        assert abs(fixed.nu_max_hz - adaptive.nu_max_hz) < 1.0

    def test_factory_horizon_guard_matches(self, leo_grid):
        """At horizon, adaptive guard equals fixed guard from factory pair."""
        fixed, adaptive = make_sband_leo_guards(leo_grid)
        r_fixed    = fixed.compute(0.0)
        r_adaptive = adaptive.compute(0.0)
        assert r_fixed.l_guard == r_adaptive.l_guard
