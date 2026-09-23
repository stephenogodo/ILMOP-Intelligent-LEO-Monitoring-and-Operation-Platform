"""
services/otfs/channel_emulator.py

OTFS LEO Satellite Channel Emulator
=====================================

Implements the complete four-component channel model for an OTFS-ISAC
waveform operating over a LEO satellite-to-ground link:

    Component 1 — Free-Space Path Loss FSPL(t)
        Dominant loss. Deterministic function of slant range and carrier
        frequency. At S-band (2.4 GHz) and 550 km altitude:
          - Zenith (550 km):   FSPL ≈ 155 dB
          - 30° elevation:     FSPL ≈ 161 dB
        Source: ILMOP OrbitalFrame.range_m per simulation frame.

    Component 2 — Rician fading h_Rician(K(θ(t)))
        The LEO satellite channel is LOS-dominated. The Rician K-factor
        varies with elevation angle — high K (approaching AWGN) near
        zenith, lower K near the horizon where ground scattering is
        possible. Rayleigh fading (K=0) is the wrong model for a
        satellite link and is not used here.
        Source: ILMOP OrbitalFrame.elevation_deg per simulation frame.

    Component 3 — Time-varying Doppler shift ν(t)
        The satellite's orbital velocity produces a Doppler shift of up
        to ±60 kHz at S-band for a 550 km circular orbit. Changes
        continuously throughout the pass. This is the channel challenge
        OTFS is specifically designed to handle.
        Source: ILMOP OrbitalFrame.range_rate_ms per simulation frame.

    Component 4 — AWGN thermal noise n(t)
        Complex Gaussian noise determined by system noise temperature and
        bandwidth. Sets the SNR floor.

Combined complex path gain per frame:

    α(t) = √(P_tx · G_tx · G_rx / FSPL(t)) · h_Rician(K(θ(t))) + n(t)

    where ν(t) = range_rate_ms(t) · f_carrier / c

All four components are physically grounded in the orbital geometry
provided by OrbitalProfileExporter. The channel is not a statistical
approximation — it is computed from the satellite's actual trajectory.

Usage
-----
    from services.otfs.channel_emulator import (
        OTFSChannelEmulator, ChannelEmulatorConfig, ChannelFrame
    )
    from services.otfs.orbital_profile import OrbitalProfileExporter

    config = ChannelEmulatorConfig(
        carrier_freq_hz    = 2.4e9,   # S-band
        bandwidth_hz       = 10e6,
        tx_power_w         = 5.0,
        tx_gain_dbi        = 6.0,
        rx_gain_dbi        = 3.0,
        noise_temp_k       = 290.0,
    )

    exporter = OrbitalProfileExporter(
        satellite_id   = "SAT-A1",
        ground_lat_deg = 52.2,
        ground_lon_deg = 0.12,
    )
    profile = exporter.export_in_view_only(start_epoch, duration_s=480)

    emulator = OTFSChannelEmulator(config)
    channel  = emulator.compute(profile)

    for frame in channel:
        print(f"FSPL={frame.fspl_db:.1f} dB  "
              f"K={frame.k_factor:.1f}  "
              f"Doppler={frame.doppler_hz:.0f} Hz  "
              f"|α|={abs(frame.path_gain):.4f}")

References
----------
- Hadani, R., Rakib, S., Tsatsanis, M., et al. (2017). Orthogonal
  time frequency space modulation. IEEE WCNC 2017.
  https://arxiv.org/abs/1808.00519
- Raviteja, P., Viterbo, E., & Hong, Y. (2018). OTFS performance on
  static multipath channels. IEEE Wireless Communications Letters,
  8(3), 745–748. https://doi.org/10.1109/LWC.2018.2890643
- Larson, W. J., & Wertz, J. R. (Eds.). (1992). Space Mission Analysis
  and Design (2nd ed.). Microcosm Press.
- Montenbruck, O., & Gill, E. (2000). Satellite Orbits. Springer.
"""

from __future__ import annotations

import cmath
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from services.otfs.orbital_profile import OrbitalFrame

# ── physical constants ──────────────────────────────────────────────────────
C_MS    = 299_792_458.0   # speed of light (m/s)
K_B     = 1.380_649e-23   # Boltzmann constant (J/K)
PI      = math.pi


# ── configuration dataclass ─────────────────────────────────────────────────

@dataclass
class ChannelEmulatorConfig:
    """
    System parameters for the OTFS LEO satellite channel emulator.

    Attributes
    ----------
    carrier_freq_hz : float
        Carrier frequency in Hz.  Default 2.4 GHz (S-band).
    bandwidth_hz : float
        System bandwidth in Hz.  Used to compute thermal noise power.
        Default 10 MHz.
    tx_power_w : float
        Transmit power in watts.  Default 5.0 W (typical LEO payload).
    tx_gain_dbi : float
        Transmit antenna gain in dBi.  Default 6.0 dBi.
    rx_gain_dbi : float
        Receive antenna gain in dBi.  Default 3.0 dBi.
    noise_temp_k : float
        System noise temperature in Kelvin.  Default 290 K (room temp).
    atm_loss_db : float
        Atmospheric absorption loss in dB.  Default 0.5 dB (S-band,
        clear sky).  Larson & Wertz (1992).
    k_factor_zenith : float
        Rician K-factor at zenith (elevation = 90°).  High K approaches
        AWGN — strong LOS, negligible scattering.  Default 20.0 dB.
    k_factor_horizon : float
        Rician K-factor at the minimum elevation angle (elevation ≈ 10°).
        Lower K — more ground scattering near horizon.  Default 6.0 dB.
    min_elevation_for_k : float
        Elevation angle (degrees) at which k_factor_horizon applies.
        Default 10.0°.
    rng_seed : Optional[int]
        Random seed for reproducible noise and fading realisations.
        None (default) uses the system random source.
    """
    carrier_freq_hz:      float = 2.4e9
    bandwidth_hz:         float = 10e6
    tx_power_w:           float = 5.0
    tx_gain_dbi:          float = 6.0
    rx_gain_dbi:          float = 3.0
    noise_temp_k:         float = 290.0
    atm_loss_db:          float = 0.5
    k_factor_zenith:      float = 20.0
    k_factor_horizon:     float = 6.0
    min_elevation_for_k:  float = 10.0
    rng_seed:             Optional[int] = None


# ── per-frame channel output ─────────────────────────────────────────────────

@dataclass
class ChannelFrame:
    """
    Complete channel characterisation for one simulation frame.

    Attributes
    ----------
    timestamp : datetime
        UTC epoch of this frame (from the OrbitalFrame).
    satellite_id : str
        Satellite identifier (from the OrbitalFrame).
    range_m : float
        Slant range in metres (from the OrbitalFrame).
    elevation_deg : float
        Elevation angle in degrees (from the OrbitalFrame).
    azimuth_deg : float
        Azimuth angle in degrees (from the OrbitalFrame — forward
        compatibility for fleet dashboard and ILP scheduler).
    fspl_db : float
        Free-space path loss in dB.  FSPL(t) = 20·log₁₀(4π·r·f/c).
    k_factor : float
        Rician K-factor (linear, not dB) for this elevation angle.
        K → ∞ corresponds to pure AWGN (perfect LOS, no scatter).
    doppler_hz : float
        Doppler frequency shift in Hz.  Positive = satellite receding.
        ν(t) = range_rate_ms · f_carrier / c.
    noise_power_w : float
        Thermal noise power in watts.  N = k_B · T · B.
    snr_db : float
        Signal-to-noise ratio in dB before fading:
        SNR = P_rx_mean / N_power
        where P_rx_mean is the mean received power after FSPL and
        atmospheric loss, before the random Rician amplitude variation.
    path_gain : complex
        Complex path gain α(t) combining the mean power envelope
        (from FSPL, Tx/Rx gains) with a Rician-distributed random
        amplitude and a uniformly distributed random phase.
        The receiver multiplies the transmitted signal by path_gain
        and adds AWGN noise to simulate the received signal.
    range_rate_ms : float
        Radial range rate in m/s (from the OrbitalFrame).
        Stored here for convenience — the navigation validator reads
        this alongside the delay estimate to compute the Doppler
        pseudorange correction.
    """
    timestamp:     datetime
    satellite_id:  str
    range_m:       float
    elevation_deg: float
    azimuth_deg:   float
    fspl_db:       float
    k_factor:      float
    doppler_hz:    float
    noise_power_w: float
    snr_db:        float
    path_gain:     complex
    range_rate_ms: float


# ── channel emulator ────────────────────────────────────────────────────────

class OTFSChannelEmulator:
    """
    Computes the four-component LEO satellite channel model from a
    sequence of OrbitalFrame records.

    Parameters
    ----------
    config : ChannelEmulatorConfig
        System parameters (carrier frequency, power, gains, noise).
    """

    def __init__(self, config: ChannelEmulatorConfig) -> None:
        self.cfg = config
        self._rng = random.Random(config.rng_seed)

        # Pre-compute derived constants
        self._wavelength_m   = C_MS / config.carrier_freq_hz
        self._tx_gain_linear = 10 ** (config.tx_gain_dbi / 10.0)
        self._rx_gain_linear = 10 ** (config.rx_gain_dbi / 10.0)
        self._atm_loss_lin   = 10 ** (config.atm_loss_db / 10.0)
        self._noise_power_w  = K_B * config.noise_temp_k * config.bandwidth_hz

    # ── public API ──────────────────────────────────────────────────────────

    def compute(self, profile: List[OrbitalFrame]) -> List[ChannelFrame]:
        """
        Compute channel frames for every OrbitalFrame in the profile.

        Only in-view frames (OrbitalFrame.in_view == True) produce
        meaningful channel estimates.  Out-of-view frames are skipped;
        the returned list may be shorter than the input profile.

        Parameters
        ----------
        profile : List[OrbitalFrame]
            Time-ordered sequence of OrbitalFrame records, typically
            from OrbitalProfileExporter.export_in_view_only().

        Returns
        -------
        List[ChannelFrame]
            One ChannelFrame per in-view OrbitalFrame.
        """
        return [
            self._compute_frame(f)
            for f in profile
            if f.in_view
        ]

    def compute_single(self, frame: OrbitalFrame) -> Optional[ChannelFrame]:
        """
        Compute a channel frame for a single OrbitalFrame.
        Returns None if the frame is not in view.
        """
        if not frame.in_view:
            return None
        return self._compute_frame(frame)

    # ── internal computation ────────────────────────────────────────────────

    def _compute_frame(self, orb: OrbitalFrame) -> ChannelFrame:
        """Compute all four channel components for one OrbitalFrame."""

        # ── Component 1: Free-space path loss ──────────────────────────────
        # FSPL(t) = 20·log₁₀(4π·r·f/c)
        # Montenbruck & Gill (2000); Larson & Wertz (1992)
        fspl_db = 20.0 * math.log10(
            4.0 * PI * orb.range_m * self.cfg.carrier_freq_hz / C_MS
        )

        # Mean received power after FSPL, atmospheric loss, and antenna gains
        # P_rx = P_tx · G_tx · G_rx / (FSPL_linear · atm_loss_linear)
        fspl_linear   = 10 ** (fspl_db / 10.0)
        p_rx_mean_w   = (
            self.cfg.tx_power_w
            * self._tx_gain_linear
            * self._rx_gain_linear
            / (fspl_linear * self._atm_loss_lin)
        )

        # ── Component 2: Rician K-factor from elevation angle ──────────────
        # K varies linearly (in dB) from k_factor_horizon at min elevation
        # to k_factor_zenith at 90°.  Converted to linear for the Rician
        # amplitude distribution.
        k_db = self._k_factor_db_from_elevation(orb.elevation_deg)
        k_linear = 10 ** (k_db / 10.0)   # linear K-factor

        # Rician envelope amplitude — mean power = p_rx_mean_w
        # For a Rician random variable X with parameter K:
        #   E[|X|²] = Ω = p_rx_mean_w
        #   s²  = K·Ω/(K+1)    (LOS power component)
        #   σ²  = Ω/(2·(K+1))  (scatter power per quadrature component)
        omega    = p_rx_mean_w
        s        = math.sqrt(k_linear * omega / (k_linear + 1.0))
        sigma    = math.sqrt(omega / (2.0 * (k_linear + 1.0)))

        # Rician amplitude: Rice(s, σ) via two independent Gaussians
        x_i = self._rng.gauss(s, sigma)   # in-phase LOS + scatter
        x_q = self._rng.gauss(0.0, sigma) # quadrature scatter only
        rician_amplitude = math.sqrt(x_i**2 + x_q**2)

        # Random phase (uniform 0–2π) for the complex path gain
        phase = self._rng.uniform(0.0, 2.0 * PI)
        path_gain = rician_amplitude * cmath.exp(1j * phase)

        # ── Component 3: Doppler shift ─────────────────────────────────────
        # ν(t) = range_rate_ms · f_carrier / c
        # Positive = satellite receding (range increasing)
        # Hadani et al. (2017); Raviteja et al. (2018)
        doppler_hz = orb.range_rate_ms * self.cfg.carrier_freq_hz / C_MS

        # ── Component 4: SNR (before noise realisation) ────────────────────
        snr_linear = p_rx_mean_w / self._noise_power_w
        snr_db     = 10.0 * math.log10(max(snr_linear, 1e-20))

        return ChannelFrame(
            timestamp     = orb.timestamp,
            satellite_id  = orb.satellite_id,
            range_m       = orb.range_m,
            elevation_deg = orb.elevation_deg,
            azimuth_deg   = orb.azimuth_deg,
            fspl_db       = fspl_db,
            k_factor      = k_linear,
            doppler_hz    = doppler_hz,
            noise_power_w = self._noise_power_w,
            snr_db        = snr_db,
            path_gain     = path_gain,
            range_rate_ms = orb.range_rate_ms,
        )

    def _k_factor_db_from_elevation(self, elevation_deg: float) -> float:
        """
        Interpolate the Rician K-factor (dB) from elevation angle.

        Linear interpolation between k_factor_horizon (at min_elevation)
        and k_factor_zenith (at 90°).  Elevation below min_elevation
        is clamped to min_elevation; above 90° clamped to 90°.

        Physical basis: at low elevation the satellite is viewed through
        more atmosphere and at a shallower angle to terrain, increasing
        ground scattering relative to the LOS component.  Near zenith
        the path is almost straight up — LOS dominates and K is high.
        Larson & Wertz (1992).
        """
        el = max(self.cfg.min_elevation_for_k, min(90.0, elevation_deg))
        t  = (el - self.cfg.min_elevation_for_k) / (90.0 - self.cfg.min_elevation_for_k)
        return self.cfg.k_factor_horizon + t * (
            self.cfg.k_factor_zenith - self.cfg.k_factor_horizon
        )
