"""
tests/test_pilot_frame.py

Test suite for waveform/pilot_frame.py.

Exit conditions for pilot_frame.py (from Waveform Design Notes):
    1. Pilot placed at correct delay-Doppler bin.
    2. Guard region surrounds pilot with no data overlap.
    3. Three regions (pilot, guard, data) partition the full frame
       with no gaps and no overlaps.
"""

import math
import numpy as np
import pytest

from waveform.otfs_signal import OTFSGrid
from waveform.pilot_frame import (
    FrameRegions,
    PilotFrame,
    PilotFrameConfig,
    make_leo_pilot_frame,
    qpsk_symbols,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def grid():
    return OTFSGrid(N=64, M=64, bandwidth_hz=10e6)


@pytest.fixture
def small_grid():
    return OTFSGrid(N=16, M=16, bandwidth_hz=10e6)


@pytest.fixture
def default_config():
    return PilotFrameConfig(k_guard_pos=4, k_guard_neg=0, l_guard=3)


@pytest.fixture
def frame(grid, default_config):
    return PilotFrame(grid, default_config)


@pytest.fixture
def rng():
    return np.random.default_rng(0)


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Pilot placement
# ══════════════════════════════════════════════════════════════════════════════

class TestPilotPlacement:

    def test_pilot_at_correct_default_position(self, grid, default_config):
        """EXIT CONDITION 1a: pilot at (k_guard_pos+1, M//2) by default."""
        pf = PilotFrame(grid, default_config)
        assert pf.pilot_k == default_config.k_guard_pos + 1
        assert pf.pilot_l == grid.M // 2

    def test_pilot_mask_has_exactly_one_true(self, frame):
        """EXIT CONDITION 1b: pilot_mask has exactly one True bin."""
        assert frame.regions.pilot_mask.sum() == 1

    def test_pilot_mask_true_at_pilot_position(self, frame):
        """Pilot mask is True at (pilot_k, pilot_l) and False elsewhere."""
        r = frame.regions
        assert r.pilot_mask[frame.pilot_k, frame.pilot_l] == True

    def test_custom_pilot_position(self, grid):
        """Manual pilot position override is respected."""
        config = PilotFrameConfig(
            k_guard_pos=3, k_guard_neg=0, l_guard=2,
            k_pilot_offset=5, l_pilot_offset=10,
        )
        pf = PilotFrame(grid, config)
        assert pf.pilot_k == 5
        assert pf.pilot_l == 10
        assert pf.regions.pilot_mask[5, 10] == True

    def test_pilot_power_boost_applied(self, frame, rng):
        """Pilot symbol power equals config.pilot_power_linear."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        actual_power = abs(X_dd[frame.pilot_k, frame.pilot_l]) ** 2
        expected_power = frame.config.pilot_power_linear * abs(frame.config.pilot_value) ** 2
        assert abs(actual_power - expected_power) < 1e-9

    def test_pilot_value_in_frame(self, frame, rng):
        """The pilot symbol at (k_pilot, l_pilot) carries the configured value."""
        pv = 1.0 + 0j
        config = PilotFrameConfig(k_guard_pos=4, l_guard=3,
                                   pilot_value=pv, pilot_power_db=0.0)
        pf = PilotFrame(frame.grid, config)
        data = qpsk_symbols(pf.n_data_symbols, rng)
        X_dd = pf.build_frame(data)
        assert abs(X_dd[pf.pilot_k, pf.pilot_l] - pv) < 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# 2 — Guard region
# ══════════════════════════════════════════════════════════════════════════════

class TestGuardRegion:

    def test_guard_surrounds_pilot_delay_positive(self, frame):
        """
        EXIT CONDITION 2a: guard extends k_guard_pos bins above the pilot
        (positive delay direction — where echoes appear).
        """
        r = frame.regions
        kp, lp = frame.pilot_k, frame.pilot_l
        cfg = frame.config
        # All bins from (kp+1, lp) to (kp+k_guard_pos, lp) must be guard
        for dk in range(1, cfg.k_guard_pos + 1):
            k = (kp + dk) % frame.grid.N
            assert r.guard_mask[k, lp] == True, \
                f"Expected guard at ({k}, {lp}) but found data"

    def test_guard_surrounds_pilot_doppler(self, frame):
        """
        EXIT CONDITION 2b: guard extends l_guard bins on both sides in Doppler.
        """
        r = frame.regions
        kp, lp = frame.pilot_k, frame.pilot_l
        M = frame.grid.M
        cfg = frame.config
        for dl in range(1, cfg.l_guard + 1):
            l_pos = (lp + dl) % M
            l_neg = (lp - dl) % M
            assert r.guard_mask[kp, l_pos] == True, \
                f"Expected guard at ({kp}, {l_pos})"
            assert r.guard_mask[kp, l_neg] == True, \
                f"Expected guard at ({kp}, {l_neg})"

    def test_no_data_overlap_with_guard(self, frame):
        """
        EXIT CONDITION 2c: data_mask and guard_mask never overlap.
        """
        assert not np.any(frame.regions.data_mask & frame.regions.guard_mask), \
            "Data and guard regions overlap"

    def test_no_data_overlap_with_pilot(self, frame):
        """Data mask does not include the pilot bin."""
        assert not np.any(frame.regions.data_mask & frame.regions.pilot_mask), \
            "Data region includes the pilot bin"

    def test_guard_zero_in_built_frame(self, frame, rng):
        """Guard region bins are zero in the built frame."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        guard_values = X_dd[frame.regions.guard_mask]
        assert np.allclose(guard_values, 0.0), \
            "Guard region contains non-zero values"

    def test_guard_size_is_correct(self, frame):
        """Guard region has exactly (k_guard_neg+1+k_guard_pos)×(2*l_guard+1)-1 bins."""
        cfg = frame.config
        expected_guard_plus_pilot = (
            (cfg.k_guard_neg + 1 + cfg.k_guard_pos)
            * (2 * cfg.l_guard + 1)
        )
        expected_guard = expected_guard_plus_pilot - 1  # subtract pilot bin
        assert frame.regions.n_guard == expected_guard, (
            f"Guard size {frame.regions.n_guard} ≠ expected {expected_guard}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Region partition
# ══════════════════════════════════════════════════════════════════════════════

class TestRegionPartition:

    def test_three_regions_cover_all_bins(self, frame):
        """
        EXIT CONDITION 3a: pilot ∪ guard ∪ data = full N×M frame.
        """
        r = frame.regions
        combined = r.pilot_mask | r.guard_mask | r.data_mask
        assert combined.all(), "Some bins not covered by any region"

    def test_regions_are_mutually_exclusive(self, frame):
        """
        EXIT CONDITION 3b: no bin belongs to more than one region.
        """
        r = frame.regions
        assert not np.any(r.pilot_mask & r.guard_mask), "Pilot/guard overlap"
        assert not np.any(r.pilot_mask & r.data_mask),  "Pilot/data overlap"
        assert not np.any(r.guard_mask & r.data_mask),  "Guard/data overlap"

    def test_total_bins_equals_NM(self, frame):
        """n_pilot + n_guard + n_data == N * M."""
        r = frame.regions
        assert r.n_total == frame.grid.N * frame.grid.M

    def test_n_data_symbols_positive(self, frame):
        """At least some data bins must exist."""
        assert frame.n_data_symbols > 0

    def test_data_fraction_reasonable(self, frame):
        """Data fraction should be between 50% and 99.9% for sensible guard sizes."""
        eta = frame.regions.data_fraction
        assert 0.5 <= eta <= 0.999, f"Data fraction {eta:.1%} out of expected range"


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Frame construction and data extraction
# ══════════════════════════════════════════════════════════════════════════════

class TestFrameConstruction:

    def test_build_frame_shape(self, frame, rng):
        """Built frame has shape (N, M)."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        assert X_dd.shape == (frame.grid.N, frame.grid.M)

    def test_data_symbols_placed_correctly(self, frame, rng):
        """Data symbols can be recovered from the built frame."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        recovered = X_dd[frame.regions.data_mask]
        np.testing.assert_allclose(recovered, data, rtol=1e-10)

    def test_extract_data_recovers_input(self, frame, rng):
        """extract_data(build_frame(data)) == data (no channel)."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        recovered = frame.extract_data(X_dd)
        np.testing.assert_allclose(recovered, data, rtol=1e-10)

    def test_wrong_data_length_raises(self, frame):
        """build_frame raises ValueError for wrong data length."""
        with pytest.raises(ValueError, match="n_data_symbols"):
            frame.build_frame(np.ones(10, dtype=complex))

    def test_pilot_override(self, frame, rng):
        """Custom pilot value override is written into the frame."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        custom_pilot = 2.0 + 1.5j
        config = PilotFrameConfig(k_guard_pos=4, l_guard=3,
                                   pilot_value=1.0+0j, pilot_power_db=0.0)
        pf = PilotFrame(frame.grid, config)
        d2 = qpsk_symbols(pf.n_data_symbols, rng)
        X_dd = pf.build_frame(d2, pilot_value=custom_pilot)
        assert X_dd[pf.pilot_k, pf.pilot_l] == custom_pilot

    def test_data_symbols_have_unit_average_power(self, rng):
        """QPSK symbols have unit average power."""
        n = 10000
        symbols = qpsk_symbols(n, rng)
        mean_power = np.mean(np.abs(symbols) ** 2)
        assert abs(mean_power - 1.0) < 0.05   # within 5% of unit power


# ══════════════════════════════════════════════════════════════════════════════
# 5 — Guard region extraction (for navigation/sensing)
# ══════════════════════════════════════════════════════════════════════════════

class TestGuardExtraction:

    def test_extract_guard_region_shape(self, frame, rng):
        """Extracted guard region has the correct shape."""
        data = qpsk_symbols(frame.n_data_symbols, rng)
        X_dd = frame.build_frame(data)
        guard, k_start, l_start = frame.extract_guard_region(X_dd)
        cfg = frame.config
        expected_rows = cfg.k_guard_neg + 1 + cfg.k_guard_pos
        expected_cols = 2 * cfg.l_guard + 1
        assert guard.shape == (expected_rows, expected_cols), (
            f"Guard shape {guard.shape} ≠ expected ({expected_rows}, {expected_cols})"
        )

    def test_pilot_visible_in_extracted_guard(self, frame, rng):
        """The pilot symbol is visible in the extracted guard region."""
        config = PilotFrameConfig(k_guard_pos=4, l_guard=3,
                                   pilot_power_db=0.0, pilot_value=1.0+0j)
        pf = PilotFrame(frame.grid, config)
        data = qpsk_symbols(pf.n_data_symbols, rng)
        X_dd = pf.build_frame(data)
        guard, k_start, l_start = pf.extract_guard_region(X_dd)
        # The pilot should be at offset (k_guard_neg, l_guard) within the guard
        k_off = config.k_guard_neg
        l_off = config.l_guard
        assert abs(guard[k_off, l_off]) > 0.9


# ══════════════════════════════════════════════════════════════════════════════
# 6 — Factory helper and spectral efficiency
# ══════════════════════════════════════════════════════════════════════════════

class TestFactoryAndEfficiency:

    def test_make_leo_pilot_frame_builds_without_error(self):
        """
        make_leo_pilot_frame creates a valid PilotFrame for standard LEO params.

        At B=10 MHz and max_doppler=62 kHz, the Doppler guard fits only when
        N ≤ B/(2×max_doppler) = 10e6/(2×62e3) ≈ 80.

        The constraint is N ≤ 80 — regardless of M — because:
            l_guard = max_doppler × N × M / B  (scales with N×M)
            For guard to fit: 2×l_guard+1 ≤ M  →  N ≤ B/(2×max_doppler)

        N=32, M=256 works:
            Δν = 10e6/(32×256) = 1221 Hz
            l_guard = ceil(62e3/1221) = 51 bins
            total guard width = 103 ≤ 256 ✓
            data fraction ≈ 60%
        """
        grid = OTFSGrid(N=32, M=256, bandwidth_hz=10e6)
        pf = make_leo_pilot_frame(
            grid,
            max_target_range_offset_m=200.0,
            max_doppler_hz=62e3,
        )
        assert pf.n_data_symbols > 0

    def test_leo_frame_guard_covers_doppler(self):
        """LEO frame guard covers at least the maximum Doppler shift."""
        grid = OTFSGrid(N=32, M=256, bandwidth_hz=10e6)
        max_doppler_hz = 62e3
        pf = make_leo_pilot_frame(grid, max_doppler_hz=max_doppler_hz)
        min_guard_bins = math.ceil(max_doppler_hz / grid.doppler_resolution_hz)
        assert pf.config.l_guard >= min_guard_bins, (
            f"l_guard={pf.config.l_guard} < required {min_guard_bins}"
        )

    def test_leo_frame_raises_for_n_too_large(self):
        """make_leo_pilot_frame raises ValueError when N > B/(2×max_doppler)."""
        grid = OTFSGrid(N=128, M=128, bandwidth_hz=10e6)   # N=128 > 80
        with pytest.raises(ValueError, match="max_doppler_hz"):
            make_leo_pilot_frame(grid, max_doppler_hz=62e3)

    def test_spectral_efficiency_increases_with_smaller_guard(self):
        """Smaller guard → higher data fraction (more spectral efficiency)."""
        grid = OTFSGrid(N=64, M=64, bandwidth_hz=10e6)
        pf_large = PilotFrame(grid, PilotFrameConfig(k_guard_pos=8, l_guard=8))
        pf_small = PilotFrame(grid, PilotFrameConfig(k_guard_pos=4, l_guard=4))
        assert pf_small.spectral_efficiency_fraction() > pf_large.spectral_efficiency_fraction()

    def test_validation_rejects_oversized_guard(self):
        """PilotFrame raises ValueError when guard exceeds grid dimensions."""
        grid = OTFSGrid(N=8, M=8, bandwidth_hz=10e6)
        with pytest.raises(ValueError):
            PilotFrame(grid, PilotFrameConfig(k_guard_pos=10, l_guard=2))

    def test_validation_rejects_pilot_too_close_to_edge(self):
        """PilotFrame raises ValueError when pilot is too close to delay edge."""
        grid = OTFSGrid(N=16, M=16, bandwidth_hz=10e6)
        with pytest.raises(ValueError):
            PilotFrame(grid, PilotFrameConfig(
                k_guard_pos=5,
                k_pilot_offset=14,   # k_p + k_guard_pos = 19 >= N=16
            ))

    def test_qpsk_symbols_length(self, rng):
        """qpsk_symbols returns exactly n symbols."""
        assert len(qpsk_symbols(100, rng)) == 100

    def test_qpsk_symbols_on_constellation(self, rng):
        """All QPSK symbols lie on the unit circle (|s|² = 1)."""
        symbols = qpsk_symbols(200, rng)
        powers = np.abs(symbols) ** 2
        np.testing.assert_allclose(powers, np.ones(200), atol=1e-10)
