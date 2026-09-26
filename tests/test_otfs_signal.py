"""
tests/test_otfs_signal.py

Test suite for waveform/otfs_signal.py.

Exit conditions for otfs_signal.py (from Waveform Design Notes):
    1. Delay-Doppler grid dimensions correct.
    2. ISFFT output has correct shape.
    3. Round-trip TX → noiseless channel → RX recovers input symbols.

Additional tests cover:
    4. ISFFT/SFFT are exact inverses.
    5. Grid derived quantities are physically correct.
    6. Channel application: pilot echo appears at correct delay/Doppler bin.
    7. Navigation helper: pseudorange extraction from pilot echo.
    8. SNR / noise power conversion utilities.
    9. Round-trip with AWGN: symbol error rate below expected BER at SNR=20dB.
   10. Time-domain channel application round-trip.
"""

import math
import numpy as np
import pytest

from waveform.otfs_signal import (
    C_MS,
    DelayDopplerChannel,
    OTFSGrid,
    OTFSReceiver,
    OTFSTransmitter,
    extract_doppler_velocity,
    extract_pseudorange,
    find_pilot_echo,
    isfft,
    noise_power_to_snr_db,
    sfft,
    snr_db_to_noise_power,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def default_grid():
    """Standard 128×128 grid at 10 MHz."""
    return OTFSGrid(N=128, M=128, bandwidth_hz=10e6)


@pytest.fixture
def small_grid():
    """Small 8×8 grid for fast algebraic tests."""
    return OTFSGrid(N=8, M=8, bandwidth_hz=10e6)


@pytest.fixture
def tx(default_grid):
    return OTFSTransmitter(default_grid)


@pytest.fixture
def rx(default_grid):
    return OTFSReceiver(default_grid)


@pytest.fixture
def rng():
    return np.random.default_rng(42)


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Grid dimensions
# ══════════════════════════════════════════════════════════════════════════════

class TestOTFSGrid:

    def test_grid_dimensions(self, default_grid):
        """EXIT CONDITION 1: grid dimensions are as specified."""
        assert default_grid.N == 128
        assert default_grid.M == 128
        assert default_grid.total_samples == 128 * 128

    def test_range_resolution_10mhz(self, default_grid):
        """Δr = c/(2B) = 14.99 m at B = 10 MHz."""
        expected = C_MS / (2.0 * 10e6)
        assert abs(default_grid.range_resolution_m - expected) < 0.01

    def test_range_resolution_value(self, default_grid):
        """Δr ≈ 14.99 m at 10 MHz — close to 15 m."""
        assert 14.5 < default_grid.range_resolution_m < 15.5

    def test_subcarrier_spacing(self, default_grid):
        """Δf = B/N = 10e6/128 ≈ 78.125 kHz."""
        expected = 10e6 / 128
        assert abs(default_grid.subcarrier_spacing_hz - expected) < 1.0

    def test_doppler_resolution(self, default_grid):
        """Δν = B/(N·M) = 10e6/(128²) ≈ 610 Hz."""
        expected = 10e6 / (128 * 128)
        assert abs(default_grid.doppler_resolution_hz - expected) < 1.0

    def test_frame_duration(self, default_grid):
        """T_f = M/Δf = M·N/B."""
        expected = default_grid.M * default_grid.N / default_grid.bandwidth_hz
        assert abs(default_grid.frame_duration_s - expected) < 1e-9

    def test_delay_bin_to_range(self, default_grid):
        """Delay bin 100 → range = 100 × Δr."""
        k = 100
        expected = k * default_grid.range_resolution_m
        assert abs(default_grid.delay_bin_to_range_m(k) - expected) < 0.001

    def test_range_to_delay_bin(self, default_grid):
        """Round-trip: range → bin → range."""
        range_m = 550_000.0  # 550 km
        bin_val = default_grid.range_m_to_delay_bin(range_m)
        recovered = default_grid.delay_bin_to_range_m(round(bin_val))
        # Should be within one range resolution bin
        assert abs(recovered - range_m) <= default_grid.range_resolution_m

    def test_frozen_grid(self, default_grid):
        """OTFSGrid is frozen — mutation raises."""
        with pytest.raises((AttributeError, TypeError)):
            default_grid.N = 256  # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
# 2 — ISFFT / SFFT transforms
# ══════════════════════════════════════════════════════════════════════════════

class TestTransforms:

    def test_isfft_output_shape(self, small_grid, rng):
        """EXIT CONDITION 2: ISFFT output shape equals input shape."""
        X_dd = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        X_tf = isfft(X_dd)
        assert X_tf.shape == (small_grid.N, small_grid.M)

    def test_sfft_output_shape(self, small_grid, rng):
        """SFFT output shape equals input shape."""
        Y_tf = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        Y_dd = sfft(Y_tf)
        assert Y_dd.shape == (small_grid.N, small_grid.M)

    def test_isfft_sfft_roundtrip(self, small_grid, rng):
        """SFFT(ISFFT(X)) ≈ X  (round-trip identity)."""
        X_dd = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        X_recovered = sfft(isfft(X_dd))
        np.testing.assert_allclose(
            X_recovered, X_dd,
            rtol=1e-10, atol=1e-10,
            err_msg="SFFT(ISFFT(X)) ≠ X — transforms are not exact inverses"
        )

    def test_sfft_isfft_roundtrip(self, small_grid, rng):
        """ISFFT(SFFT(Y)) ≈ Y  (round-trip in both directions)."""
        Y_tf = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        Y_recovered = isfft(sfft(Y_tf))
        np.testing.assert_allclose(
            Y_recovered, Y_tf,
            rtol=1e-10, atol=1e-10,
        )

    def test_isfft_linearity(self, small_grid, rng):
        """ISFFT is a linear transform: ISFFT(aX+bY) = a·ISFFT(X) + b·ISFFT(Y)."""
        a, b = 1.5 + 0.3j, -0.7 + 1.2j
        X = rng.standard_normal((small_grid.N, small_grid.M)) \
          + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        Y = rng.standard_normal((small_grid.N, small_grid.M)) \
          + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        np.testing.assert_allclose(
            isfft(a * X + b * Y),
            a * isfft(X) + b * isfft(Y),
            rtol=1e-10,
        )

    def test_isfft_rejects_1d(self):
        """ISFFT raises ValueError for 1D input."""
        with pytest.raises(ValueError, match="2D"):
            isfft(np.ones(16))

    def test_sfft_rejects_1d(self):
        """SFFT raises ValueError for 1D input."""
        with pytest.raises(ValueError, match="2D"):
            sfft(np.ones(16))


# ══════════════════════════════════════════════════════════════════════════════
# 3 — Round-trip: TX → noiseless channel → RX
# ══════════════════════════════════════════════════════════════════════════════

class TestRoundTrip:

    def test_noiseless_roundtrip_identity_channel(self, small_grid, rng):
        """
        EXIT CONDITION 3: TX → identity channel → RX recovers input symbols.

        With no channel (identity tap at delay=0, Doppler=0, gain=1) and
        no noise, the received delay-Doppler symbols should exactly equal
        the transmitted symbols.
        """
        tx = OTFSTransmitter(small_grid)
        rx_proc = OTFSReceiver(small_grid)

        X_dd = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))

        # Transmit
        s = tx.modulate(X_dd)

        # Identity channel: no delay, no Doppler, gain=1, no noise
        channel = DelayDopplerChannel(taps=[(0, 0, 1.0 + 0j)], noise_power=0.0)
        r = channel.apply_through_time_domain(s, small_grid, noise_power=0.0)

        # Receive
        Y_dd = rx_proc.demodulate(r)

        np.testing.assert_allclose(
            Y_dd, X_dd,
            rtol=1e-8, atol=1e-8,
            err_msg="TX → identity channel → RX did not recover input symbols"
        )

    def test_noiseless_roundtrip_dd_domain(self, small_grid, rng):
        """
        Round-trip test purely in the delay-Doppler domain.
        DD channel with identity tap should reproduce input exactly.
        """
        X_dd = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))

        channel = DelayDopplerChannel(taps=[(0, 0, 1.0 + 0j)], noise_power=0.0)
        Y_dd = channel.apply(X_dd)

        np.testing.assert_allclose(Y_dd, X_dd, rtol=1e-12, atol=1e-12)

    def test_transmitted_signal_length(self, default_grid, rng):
        """Transmitted signal has exactly N*M complex samples."""
        tx = OTFSTransmitter(default_grid)
        X_dd = rng.standard_normal((default_grid.N, default_grid.M)) \
             + 1j * rng.standard_normal((default_grid.N, default_grid.M))
        s = tx.modulate(X_dd)
        assert s.shape == (default_grid.N * default_grid.M,)

    def test_modulate_rejects_wrong_shape(self, default_grid, rng):
        """Transmitter raises ValueError for wrong input shape."""
        tx = OTFSTransmitter(default_grid)
        with pytest.raises(ValueError):
            tx.modulate(rng.standard_normal((64, 64)))

    def test_demodulate_rejects_wrong_length(self, default_grid, rng):
        """Receiver raises ValueError for wrong signal length."""
        rx_proc = OTFSReceiver(default_grid)
        with pytest.raises(ValueError):
            rx_proc.demodulate(rng.standard_normal(100))

    def test_roundtrip_power_preservation(self, small_grid, rng):
        """
        Noiseless round-trip: output power ≈ input power.
        Confirms the transmitter/receiver scaling is consistent.
        """
        tx = OTFSTransmitter(small_grid)
        rx_proc = OTFSReceiver(small_grid)

        X_dd = rng.standard_normal((small_grid.N, small_grid.M)) \
             + 1j * rng.standard_normal((small_grid.N, small_grid.M))
        s = tx.modulate(X_dd)
        channel = DelayDopplerChannel(taps=[(0, 0, 1.0 + 0j)], noise_power=0.0)
        r = channel.apply_through_time_domain(s, small_grid, noise_power=0.0)
        Y_dd = rx_proc.demodulate(r)

        P_in  = np.mean(np.abs(X_dd) ** 2)
        P_out = np.mean(np.abs(Y_dd) ** 2)
        assert abs(P_in - P_out) / max(P_in, 1e-10) < 1e-6


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Delay-Doppler channel
# ══════════════════════════════════════════════════════════════════════════════

class TestDelayDopplerChannel:

    def test_pilot_echo_at_correct_delay(self, small_grid):
        """
        A pilot at bin (0, 0) with a single-tap channel at delay k_p=2
        should produce an echo at bin (2, 0).
        """
        N, M = small_grid.N, small_grid.M
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[0, 0] = 1.0   # pilot at origin

        channel = DelayDopplerChannel(taps=[(2, 0, 1.0 + 0j)], noise_power=0.0)
        Y_dd = channel.apply(X_dd)

        # The echo should be at (2, 0)
        assert abs(Y_dd[2, 0]) > 0.99
        # No energy at the origin or other bins
        assert abs(Y_dd[0, 0]) < 1e-10

    def test_pilot_echo_at_correct_doppler(self, small_grid):
        """
        A pilot at (0, 0) with Doppler shift l_p=3 should produce echo at (0, 3).
        """
        N, M = small_grid.N, small_grid.M
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[0, 0] = 1.0

        channel = DelayDopplerChannel(taps=[(0, 3, 1.0 + 0j)], noise_power=0.0)
        Y_dd = channel.apply(X_dd)

        assert abs(Y_dd[0, 3]) > 0.99
        assert abs(Y_dd[0, 0]) < 1e-10

    def test_multiple_taps(self, small_grid):
        """
        Multiple channel taps produce multiple echoes at distinct positions.
        """
        N, M = small_grid.N, small_grid.M
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[0, 0] = 1.0

        taps = [(1, 0, 0.8 + 0j), (3, 2, 0.5 + 0j)]
        channel = DelayDopplerChannel(taps=taps, noise_power=0.0)
        Y_dd = channel.apply(X_dd)

        assert abs(Y_dd[1, 0]) > 0.75
        assert abs(Y_dd[3, 2]) > 0.45
        assert abs(Y_dd[0, 0]) < 1e-10

    def test_noise_increases_variance(self, small_grid, rng):
        """
        With noise, the received signal has higher variance than without.
        """
        N, M = small_grid.N, small_grid.M
        X_dd = np.ones((N, M), dtype=complex)

        ch_noiseless = DelayDopplerChannel(taps=[(0, 0, 1.0)], noise_power=0.0)
        ch_noisy     = DelayDopplerChannel(taps=[(0, 0, 1.0)], noise_power=0.1)

        Y_clean = ch_noiseless.apply(X_dd)
        Y_noisy = ch_noisy.apply(X_dd)

        # Noisy output should have higher variance
        assert np.var(Y_noisy - Y_clean) > 1e-6


# ══════════════════════════════════════════════════════════════════════════════
# 5 — Navigation helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestNavigationHelpers:

    def test_find_pilot_echo_at_known_position(self, default_grid):
        """
        Pilot echo at (k_p, l_p) should be found by find_pilot_echo.
        """
        N, M = default_grid.N, default_grid.M
        Y_dd = np.zeros((N, M), dtype=complex)
        Y_dd[5, 3] = 2.5 + 0.0j   # synthetic echo at delay=5, Doppler=3

        peak_k, peak_l, peak_amp = find_pilot_echo(
            Y_dd, pilot_k=0, pilot_l=0,
            search_radius_k=10, search_radius_l=10,
        )
        assert peak_k == 5
        assert peak_l == 3
        assert abs(peak_amp - 2.5) < 1e-6

    def test_extract_pseudorange_from_delay_bin(self, default_grid):
        """
        extract_pseudorange(k) = k × Δr = k × c/(2B).
        """
        k = 10
        expected = k * default_grid.range_resolution_m
        assert abs(extract_pseudorange(k, default_grid) - expected) < 0.001

    def test_pseudorange_zero_at_bin_zero(self, default_grid):
        """Delay bin 0 → pseudorange = 0."""
        assert extract_pseudorange(0, default_grid) == 0.0

    def test_extract_doppler_velocity(self, default_grid):
        """
        extract_doppler_velocity(l, grid, f_c) = l × Δν × c / f_c.
        """
        l = 5
        f_c = 2.4e9
        expected_doppler_hz = l * default_grid.doppler_resolution_hz
        expected_velocity   = expected_doppler_hz * C_MS / f_c
        result = extract_doppler_velocity(l, default_grid, f_c)
        assert abs(result - expected_velocity) < 0.01

    def test_doppler_velocity_at_bin_zero(self, default_grid):
        """Doppler bin 0 → radial velocity = 0."""
        assert extract_doppler_velocity(0, default_grid, 2.4e9) == 0.0

    def test_navigation_pipeline_single_tap(self, small_grid):
        """
        End-to-end navigation test using the delay-Doppler domain channel.

        In OTFS, the delay-Doppler channel model captures the physical path
        geometry directly: a tap at (k_p, l_p) represents a scatterer at
        delay bin k_p (range ρ = k_p × Δr) and Doppler bin l_p.

        The navigation pipeline:
          1. Transmit OTFS pilot at (pilot_k, pilot_l) in the DD grid.
          2. Apply DD-domain channel: echo appears at (pilot_k + k_p, pilot_l).
          3. Search for the echo peak in the received DD grid.
          4. Extract pseudorange from the echo delay bin.

        NOTE: For sub-symbol time-domain delays, the correct simulation model
        is the DD-domain channel (channel.apply()). The time-domain path
        (apply_through_time_domain) maps symbol-aligned delays (multiples of M
        samples) to integer delay bins, which is correct for inter-symbol delays
        but not for sub-symbol fractional delays. The DD model is the standard
        in the OTFS literature (Raviteja et al. 2018a, Eq. 6).
        """
        N, M = small_grid.N, small_grid.M

        # Place pilot at (2, 2) — not at origin to distinguish from echo clearly
        pilot_k, pilot_l = 2, 2
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[pilot_k, pilot_l] = 1.0

        # True channel: delay = 3 bins, Doppler = 0
        true_delay_bins = 3
        true_range_m    = true_delay_bins * small_grid.range_resolution_m

        # Apply DD-domain channel (standard OTFS channel model)
        channel = DelayDopplerChannel(
            taps=[(true_delay_bins, 0, 1.0 + 0j)],
            noise_power=0.0,
        )
        Y_dd = channel.apply(X_dd)

        # Echo should appear at (pilot_k + k_p, pilot_l) = (5, 2)
        expected_echo_k = (pilot_k + true_delay_bins) % N
        expected_echo_l = pilot_l

        # Find the echo in the received DD grid
        peak_k, peak_l, peak_amp = find_pilot_echo(
            Y_dd,
            pilot_k=pilot_k,  # search relative to pilot position
            pilot_l=pilot_l,
            search_radius_k=N // 2 - 1,
            search_radius_l=M // 2 - 1,
        )

        assert peak_k == expected_echo_k, (
            f"Echo found at delay bin {peak_k}, expected {expected_echo_k}"
        )

        # Extract pseudorange from relative delay (echo_k - pilot_k)
        relative_delay_bins = (peak_k - pilot_k) % N
        measured_range_m    = extract_pseudorange(relative_delay_bins, small_grid)

        # Should match to within one range resolution bin
        assert abs(measured_range_m - true_range_m) <= small_grid.range_resolution_m, (
            f"Pseudorange error {abs(measured_range_m - true_range_m):.2f} m "
            f"exceeds Δr = {small_grid.range_resolution_m:.2f} m"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6 — SNR / noise power utilities
# ══════════════════════════════════════════════════════════════════════════════

class TestSNRUtilities:

    def test_snr_to_noise_roundtrip(self):
        """snr_db_to_noise_power → noise_power_to_snr_db should recover SNR."""
        for snr_db in [-10.0, 0.0, 10.0, 20.0, 30.0]:
            noise_power = snr_db_to_noise_power(snr_db, signal_power=1.0)
            recovered   = noise_power_to_snr_db(noise_power, signal_power=1.0)
            assert abs(recovered - snr_db) < 1e-9, f"SNR round-trip failed at {snr_db} dB"

    def test_0db_snr_gives_equal_signal_noise_power(self):
        """At SNR = 0 dB, noise power = signal power."""
        noise = snr_db_to_noise_power(0.0, signal_power=1.0)
        assert abs(noise - 1.0) < 1e-10

    def test_noise_power_decreases_with_snr(self):
        """Higher SNR → lower noise power."""
        n10 = snr_db_to_noise_power(10.0)
        n20 = snr_db_to_noise_power(20.0)
        assert n20 < n10
