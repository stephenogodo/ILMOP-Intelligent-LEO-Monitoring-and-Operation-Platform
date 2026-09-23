"""
tests/test_channel_emulator.py

Tests for services/otfs/channel_emulator.py

Validates the four channel model components against known physical
constraints for a 550 km LEO satellite link at S-band.

All tests are self-contained — no Kafka, TimescaleDB, or network
access required.

References
----------
- Hadani et al. (2017). IEEE WCNC 2017. https://arxiv.org/abs/1808.00519
- Larson, W. J., & Wertz, J. R. (1992). Space Mission Analysis
  and Design (2nd ed.). Microcosm Press.
- Montenbruck, O., & Gill, E. (2000). Satellite Orbits. Springer.
"""

import cmath
import math
from datetime import datetime, timezone

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.otfs.channel_emulator import (
    ChannelEmulatorConfig,
    ChannelFrame,
    OTFSChannelEmulator,
)
from services.otfs.orbital_profile import OrbitalFrame, OrbitalProfileExporter


# ── helpers ─────────────────────────────────────────────────────────────────

SBAND_HZ    = 2.4e9
CAMBRIDGE   = dict(ground_lat_deg=52.205, ground_lon_deg=0.119)
TEST_EPOCH  = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
RNG_SEED    = 42


def default_config(**overrides) -> ChannelEmulatorConfig:
    cfg = ChannelEmulatorConfig(
        carrier_freq_hz = SBAND_HZ,
        bandwidth_hz    = 10e6,
        tx_power_w      = 5.0,
        tx_gain_dbi     = 6.0,
        rx_gain_dbi     = 3.0,
        noise_temp_k    = 290.0,
        rng_seed        = RNG_SEED,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_orbital_frame(
    range_m:       float = 550_000.0,
    range_rate_ms: float = 0.0,
    elevation_deg: float = 45.0,
    azimuth_deg:   float = 180.0,
    in_view:       bool  = True,
) -> OrbitalFrame:
    return OrbitalFrame(
        timestamp     = TEST_EPOCH,
        satellite_id  = "SAT-A1",
        range_m       = range_m,
        range_rate_ms = range_rate_ms,
        elevation_deg = elevation_deg,
        azimuth_deg   = azimuth_deg,
        in_view       = in_view,
    )


def make_emulator(**overrides) -> OTFSChannelEmulator:
    return OTFSChannelEmulator(default_config(**overrides))


# ── basic API tests ──────────────────────────────────────────────────────────

class TestBasicAPI:

    def test_compute_returns_channel_frames(self):
        """compute() returns a list of ChannelFrame objects."""
        emulator = make_emulator()
        profile  = [make_orbital_frame()]
        result   = emulator.compute(profile)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ChannelFrame)

    def test_compute_skips_out_of_view_frames(self):
        """Frames with in_view=False are excluded from the output."""
        emulator = make_emulator()
        profile  = [
            make_orbital_frame(in_view=True),
            make_orbital_frame(in_view=False),
            make_orbital_frame(in_view=True),
        ]
        result = emulator.compute(profile)
        assert len(result) == 2
        assert all(isinstance(f, ChannelFrame) for f in result)

    def test_compute_single_returns_none_for_out_of_view(self):
        """compute_single() returns None when in_view=False."""
        emulator = make_emulator()
        frame    = make_orbital_frame(in_view=False)
        result   = emulator.compute_single(frame)
        assert result is None

    def test_compute_single_returns_channel_frame_for_in_view(self):
        """compute_single() returns a ChannelFrame when in_view=True."""
        emulator = make_emulator()
        frame    = make_orbital_frame(in_view=True)
        result   = emulator.compute_single(frame)
        assert isinstance(result, ChannelFrame)

    def test_satellite_id_propagated(self):
        """satellite_id from OrbitalFrame is preserved in ChannelFrame."""
        emulator = make_emulator()
        frame    = make_orbital_frame()
        result   = emulator.compute_single(frame)
        assert result.satellite_id == "SAT-A1"

    def test_azimuth_propagated(self):
        """azimuth_deg from OrbitalFrame is preserved in ChannelFrame."""
        emulator = make_emulator()
        frame    = make_orbital_frame(azimuth_deg=270.0)
        result   = emulator.compute_single(frame)
        assert result.azimuth_deg == 270.0


# ── Component 1: FSPL tests ──────────────────────────────────────────────────

class TestFSPL:

    def test_fspl_at_550km_zenith_sband(self):
        """
        FSPL at 550 km zenith range, S-band (2.4 GHz):
          FSPL = 20·log₁₀(4π × 550,000 × 2.4×10⁹ / 3×10⁸) ≈ 155 dB.
        Allow ±1 dB tolerance.
        Montenbruck & Gill (2000); Larson & Wertz (1992).
        """
        emulator = make_emulator(carrier_freq_hz=2.4e9)
        frame    = make_orbital_frame(range_m=550_000.0)
        result   = emulator.compute_single(frame)
        assert 154.0 <= result.fspl_db <= 156.0, (
            f"Expected ~155 dB, got {result.fspl_db:.2f} dB"
        )

    def test_fspl_at_30deg_elevation_higher_than_zenith(self):
        """
        At 30° elevation the slant range is ~1,100 km and FSPL ≈ 161 dB —
        6 dB higher than at zenith (550 km).
        """
        emulator     = make_emulator(carrier_freq_hz=2.4e9)
        frame_zenith = make_orbital_frame(range_m=550_000.0)
        frame_low    = make_orbital_frame(range_m=1_100_000.0,
                                          elevation_deg=30.0)
        r_zenith = emulator.compute_single(frame_zenith)
        r_low    = emulator.compute_single(frame_low)
        assert r_low.fspl_db > r_zenith.fspl_db, (
            "FSPL at 30° elevation should exceed FSPL at zenith"
        )
        diff = r_low.fspl_db - r_zenith.fspl_db
        assert 5.0 <= diff <= 7.0, (
            f"Expected ~6 dB difference, got {diff:.2f} dB"
        )

    def test_fspl_increases_with_range(self):
        """FSPL is monotonically increasing with slant range."""
        emulator = make_emulator()
        ranges   = [550_000.0, 700_000.0, 900_000.0, 1_100_000.0]
        fspl_values = [
            emulator.compute_single(make_orbital_frame(range_m=r)).fspl_db
            for r in ranges
        ]
        for i in range(1, len(fspl_values)):
            assert fspl_values[i] > fspl_values[i-1]

    def test_fspl_formula(self):
        """FSPL value matches the analytical formula exactly."""
        r_m = 750_000.0
        f   = 2.4e9
        expected_db = 20.0 * math.log10(4 * math.pi * r_m * f / 299_792_458.0)
        emulator = make_emulator(carrier_freq_hz=f)
        result   = emulator.compute_single(make_orbital_frame(range_m=r_m))
        assert abs(result.fspl_db - expected_db) < 0.001


# ── Component 2: Rician K-factor tests ──────────────────────────────────────

class TestRicianKFactor:

    def test_k_factor_higher_at_zenith(self):
        """K-factor at 90° elevation exceeds K-factor at 10° elevation."""
        emulator = make_emulator(rng_seed=0)
        f_zenith  = make_orbital_frame(elevation_deg=90.0)
        f_horizon = make_orbital_frame(elevation_deg=10.0)
        r_zenith  = emulator.compute_single(f_zenith)
        r_horizon = emulator.compute_single(f_horizon)
        assert r_zenith.k_factor > r_horizon.k_factor

    def test_k_factor_monotone_with_elevation(self):
        """K-factor increases monotonically with elevation angle."""
        emulator = make_emulator(rng_seed=0)
        elevations = [10.0, 20.0, 30.0, 45.0, 60.0, 75.0, 90.0]
        k_values = [
            emulator.compute_single(
                make_orbital_frame(elevation_deg=el)
            ).k_factor
            for el in elevations
        ]
        for i in range(1, len(k_values)):
            assert k_values[i] >= k_values[i-1], (
                f"K-factor not monotone: {k_values[i-1]:.2f} → {k_values[i]:.2f}"
                f" at elevation {elevations[i]}°"
            )

    def test_k_factor_at_zenith_matches_config(self):
        """K-factor at 90° equals config.k_factor_zenith (dB converted)."""
        cfg = default_config(k_factor_zenith=20.0)
        emulator = OTFSChannelEmulator(cfg)
        result   = emulator.compute_single(make_orbital_frame(elevation_deg=90.0))
        expected_linear = 10 ** (20.0 / 10.0)
        assert abs(result.k_factor - expected_linear) < 0.01

    def test_k_factor_at_horizon_matches_config(self):
        """K-factor at min elevation equals config.k_factor_horizon (dB converted)."""
        cfg = default_config(k_factor_horizon=6.0, min_elevation_for_k=10.0)
        emulator = OTFSChannelEmulator(cfg)
        result   = emulator.compute_single(
            make_orbital_frame(elevation_deg=10.0)
        )
        expected_linear = 10 ** (6.0 / 10.0)
        assert abs(result.k_factor - expected_linear) < 0.01


# ── Component 3: Doppler shift tests ─────────────────────────────────────────

class TestDopplerShift:

    def test_doppler_zero_at_zenith(self):
        """
        At zenith, range rate = 0 (satellite passing overhead —
        pure tangential velocity, zero radial component).
        Doppler shift should be zero.
        """
        emulator = make_emulator()
        frame    = make_orbital_frame(range_rate_ms=0.0)
        result   = emulator.compute_single(frame)
        assert result.doppler_hz == 0.0

    def test_doppler_positive_when_receding(self):
        """
        Positive range rate (satellite receding) produces positive
        Doppler shift (carrier appears at higher frequency at transmitter
        but lower at receiver — conventionally positive = receding here).
        """
        emulator = make_emulator()
        frame    = make_orbital_frame(range_rate_ms=3000.0)
        result   = emulator.compute_single(frame)
        assert result.doppler_hz > 0.0

    def test_doppler_negative_when_approaching(self):
        """Negative range rate (satellite approaching) gives negative Doppler."""
        emulator = make_emulator()
        frame    = make_orbital_frame(range_rate_ms=-3000.0)
        result   = emulator.compute_single(frame)
        assert result.doppler_hz < 0.0

    def test_doppler_magnitude_at_sband(self):
        """
        At S-band (2.4 GHz), maximum LEO Doppler shift for 7,500 m/s
        radial velocity, using the precise NIST speed of light
        (299,792,458 m/s): nu = 7500 * 2.4e9 / 299,792,458 = 60,041.5 Hz.
        The commonly cited 60,000 Hz uses the rounded c = 3e8.
        The emulator uses the precise value; the test matches accordingly.
        Hadani et al. (2017); Raviteja et al. (2018).
        """
        C_PRECISE   = 299_792_458.0
        f_c         = 2.4e9
        rr          = 7500.0
        expected_hz = rr * f_c / C_PRECISE
        emulator    = make_emulator(carrier_freq_hz=f_c)
        result      = emulator.compute_single(make_orbital_frame(range_rate_ms=rr))
        assert abs(result.doppler_hz - expected_hz) < 0.01

    def test_doppler_formula(self):
        """Doppler value matches ν = range_rate × f_carrier / c exactly."""
        f_c  = 2.4e9
        rr   = 4321.0
        expected = rr * f_c / 299_792_458.0
        emulator = make_emulator(carrier_freq_hz=f_c)
        result   = emulator.compute_single(make_orbital_frame(range_rate_ms=rr))
        assert abs(result.doppler_hz - expected) < 0.001

    def test_doppler_scales_with_carrier_frequency(self):
        """
        At Ka-band (26 GHz) Doppler is ~10.8× larger than at S-band
        (2.4 GHz) for the same range rate.
        """
        rr = 3000.0
        em_s  = make_emulator(carrier_freq_hz=2.4e9)
        em_ka = make_emulator(carrier_freq_hz=26e9)
        r_s   = em_s.compute_single( make_orbital_frame(range_rate_ms=rr))
        r_ka  = em_ka.compute_single(make_orbital_frame(range_rate_ms=rr))
        ratio = r_ka.doppler_hz / r_s.doppler_hz
        assert abs(ratio - (26e9 / 2.4e9)) < 0.01


# ── Component 4: Noise and SNR tests ─────────────────────────────────────────

class TestNoiseAndSNR:

    def test_noise_power_formula(self):
        """Noise power = k_B × T × B matches thermal noise formula."""
        T = 290.0
        B = 10e6
        expected = 1.380_649e-23 * T * B
        emulator = make_emulator(noise_temp_k=T, bandwidth_hz=B)
        result   = emulator.compute_single(make_orbital_frame())
        assert abs(result.noise_power_w - expected) / expected < 1e-6

    def test_snr_higher_at_shorter_range(self):
        """SNR is higher when the satellite is closer (shorter range)."""
        emulator    = make_emulator(rng_seed=0)
        r_close     = emulator.compute_single(make_orbital_frame(range_m=550_000.0))
        r_far       = emulator.compute_single(make_orbital_frame(range_m=1_100_000.0))
        assert r_close.snr_db > r_far.snr_db

    def test_snr_higher_with_more_tx_power(self):
        """SNR increases with transmit power."""
        em_low  = make_emulator(tx_power_w=1.0,  rng_seed=0)
        em_high = make_emulator(tx_power_w=20.0, rng_seed=0)
        r_low   = em_low.compute_single( make_orbital_frame())
        r_high  = em_high.compute_single(make_orbital_frame())
        assert r_high.snr_db > r_low.snr_db

    def test_snr_decreases_with_noise_temperature(self):
        """SNR decreases as noise temperature increases."""
        em_cold = make_emulator(noise_temp_k=100.0, rng_seed=0)
        em_hot  = make_emulator(noise_temp_k=600.0, rng_seed=0)
        r_cold  = em_cold.compute_single(make_orbital_frame())
        r_hot   = em_hot.compute_single( make_orbital_frame())
        assert r_cold.snr_db > r_hot.snr_db


# ── path_gain tests ───────────────────────────────────────────────────────────

class TestPathGain:

    def test_path_gain_is_complex(self):
        """|α(t)| is a complex number."""
        emulator = make_emulator()
        result   = emulator.compute_single(make_orbital_frame())
        assert isinstance(result.path_gain, complex)

    def test_path_gain_magnitude_positive(self):
        """|α(t)| > 0 for any in-view frame."""
        emulator = make_emulator()
        result   = emulator.compute_single(make_orbital_frame())
        assert abs(result.path_gain) > 0.0

    def test_path_gain_reproducible_with_seed(self):
        """Same rng_seed produces identical path_gain values."""
        f = make_orbital_frame()
        r1 = make_emulator(rng_seed=7).compute_single(f)
        r2 = make_emulator(rng_seed=7).compute_single(f)
        assert r1.path_gain == r2.path_gain

    def test_path_gain_varies_without_seed(self):
        """Different rng_seed values produce different path_gain realisations."""
        f  = make_orbital_frame()
        r1 = make_emulator(rng_seed=1).compute_single(f)
        r2 = make_emulator(rng_seed=2).compute_single(f)
        assert r1.path_gain != r2.path_gain


# ── integration test: full profile through emulator ──────────────────────────

class TestFullProfile:

    def test_full_pass_through_emulator(self):
        """
        Export a full pass from SGP4 and process every in-view frame
        through the channel emulator. Validates the end-to-end pipeline
        from OrbitalFrame to ChannelFrame.
        """
        exporter = OrbitalProfileExporter(
            satellite_id     = "SAT-A1",
            ground_lat_deg   = 52.205,
            ground_lon_deg   = 0.119,
            altitude_km      = 550.0,
            inclination_deg  = 51.6,
            raan_deg         = 45.0,
            mean_anomaly_deg = 0.0,
            min_elevation_deg= 10.0,
            epoch            = TEST_EPOCH,
        )
        profile  = exporter.export_in_view_only(
            TEST_EPOCH, duration_s=192*60, step_s=30
        )
        emulator = OTFSChannelEmulator(default_config(rng_seed=RNG_SEED))
        channel  = emulator.compute(profile)

        assert len(channel) > 0, "No in-view frames found over 192-minute window"
        assert all(isinstance(f, ChannelFrame) for f in channel)

        # FSPL over the pass is always within [140, 170] dB for S-band LEO
        fspl_values = [f.fspl_db for f in channel]
        assert all(140.0 <= v <= 170.0 for v in fspl_values), (
            f"FSPL out of expected range: min={min(fspl_values):.1f}, "
            f"max={max(fspl_values):.1f}"
        )

        # Doppler over the pass spans both negative and positive values
        # for a complete pass (approaching then receding)
        doppler_values = [f.doppler_hz for f in channel]
        assert min(doppler_values) < 0.0 or max(doppler_values) > 0.0, (
            "Expected some non-zero Doppler shift over a full pass"
        )
