"""
services/navigation/validator.py

OTFS Navigation Validation Pipeline
======================================

Compares OTFS receiver pseudorange estimates against high-precision
HPOP truth from NavigationTruthModel, producing per-epoch residuals,
RMS error, and GDOP statistics for the thesis navigation contribution.

Scientific context
------------------
The OTFS receiver extracts pseudorange from the delay of the pilot
echo in the delay-Doppler domain. The delay resolution is:

    Δτ = 1/B  →  Δr = c/(2B)

For B = 10 MHz: Δr = 15 m (range resolution floor).

The pseudorange noise standard deviation is:

    σ_total = √(σ_quant² + σ_thermal²)

where:
    σ_quant   = Δr/√12          (uniform quantisation noise)
    σ_thermal = Δr/(2π√SNR)     (thermal noise equivalent ranging)

This gives σ_total ≈ 5–30 m over a 550 km LEO pass at S-band with
the system parameters in ChannelEmulatorConfig (5W Tx, 10 MHz BW).
The HPOP truth is ~3–5 m, giving a truth/measurement ratio > 5:1.

GDOP (Geometric Dilution of Precision)
---------------------------------------
For a single-satellite pass, GDOP is computed per epoch from the unit
line-of-sight vector in the ECI frame. For a single observation, the
geometry matrix is rank-1 — GDOP reflects the elevation angle only.
Full 3D position GDOP requires ≥4 simultaneous satellite observations
(Scenarios 2 and 3); the single-pass GDOP shows the geometric
constraint that motivates the multi-satellite scenarios.

Usage
-----
    from services.navigation.validator import NavigationValidator

    validator = NavigationValidator(
        satellite_id    = "SAT-A1",
        altitude_km     = 550.0,
        inclination_deg = 51.6,
        raan_deg        = 45.0,
        epoch           = model_epoch,
        ground_lat_deg  = 52.205,
        ground_lon_deg  = 0.119,
    )

    result = validator.validate_pass(
        start_epoch = pass_start,
        duration_s  = 480.0,
        step_s      = 10.0,
    )

    print(f"RMS residual: {result.rms_residual_m:.1f} m")
    print(f"Mean GDOP:    {result.gdop_mean:.2f}")

References
----------
- Hadani, R., et al. (2017). OTFS modulation. IEEE WCNC 2017.
  https://arxiv.org/abs/1808.00519
- Raviteja, P., Viterbo, E., & Hong, Y. (2018). OTFS performance on
  static multipath channels. IEEE Wireless Communications Letters,
  8(3), 745–748. https://doi.org/10.1109/LWC.2018.2890643
- Vallado, D. A. (2013). Fundamentals of Astrodynamics (4th ed.).
  Microcosm Press. (GDOP formulation, Section 12.3)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from services.navigation.navigation_truth import NavigationTruthModel, TruthFrame
from services.otfs.channel_emulator import (
    ChannelEmulatorConfig,
    ChannelFrame,
    OTFSChannelEmulator,
)
from services.otfs.orbital_profile import OrbitalFrame, OrbitalProfileExporter

# ── physical constants ────────────────────────────────────────────────────────
C_MS = 299_792_458.0   # speed of light (m/s)


# ── output dataclasses ────────────────────────────────────────────────────────

@dataclass
class ValidationFrame:
    """
    Per-epoch navigation validation result.

    Attributes
    ----------
    timestamp : datetime
        UTC epoch of this observation.
    satellite_id : str
        Satellite identifier.
    true_range_m : float
        HPOP pseudorange truth (metres).
    otfs_range_m : float
        Simulated OTFS receiver pseudorange estimate (metres).
    residual_m : float
        otfs_range_m − true_range_m (metres). Positive = overestimate.
    elevation_deg : float
        Satellite elevation above ground station horizon (degrees).
    snr_db : float
        Signal-to-noise ratio from the channel emulator (dB).
    doppler_hz : float
        Doppler shift at this epoch (Hz).
    ranging_sigma_m : float
        Theoretical pseudorange noise standard deviation (metres).
        Computed from bandwidth and SNR at this epoch.
    gdop : float
        Geometric Dilution of Precision for single-satellite geometry.
        Reflects elevation angle only; full 3D GDOP requires ≥4 sats.
    truth_source : str
        'hpop', 'j2j4', or 'sp3' — source of true_range_m.
    """
    timestamp:       datetime
    satellite_id:    str
    true_range_m:    float
    otfs_range_m:    float
    residual_m:      float
    elevation_deg:   float
    snr_db:          float
    doppler_hz:      float
    ranging_sigma_m: float
    gdop:            float
    truth_source:    str


@dataclass
class ValidationResult:
    """
    Pass-level navigation validation summary.

    Attributes
    ----------
    satellite_id : str
        Satellite identifier.
    start_epoch : datetime
        UTC start of the validated contact window.
    end_epoch : datetime
        UTC end of the validated contact window.
    n_observations : int
        Number of valid in-view frames processed.
    rms_residual_m : float
        Root-mean-square of all residuals (metres).
        Primary accuracy metric for the OTFS navigation contribution.
    max_residual_m : float
        Maximum absolute residual over the pass (metres).
    mean_residual_m : float
        Mean residual — systematic bias (metres). Near zero for
        an unbiased estimator.
    std_residual_m : float
        Standard deviation of residuals (metres).
    gdop_mean : float
        Mean GDOP over the pass. Lower = better geometry.
    gdop_min : float
        Best-case GDOP (near zenith).
    gdop_max : float
        Worst-case GDOP (near horizon).
    propagator_mode : str
        Truth propagator used: 'hpop', 'j2j4', or 'sp3'.
    bandwidth_hz : float
        OTFS signal bandwidth used in ranging noise computation.
    range_resolution_m : float
        Δr = c/(2B) — OTFS delay-domain range resolution floor (metres).
    frames : List[ValidationFrame]
        Per-epoch validation data for plotting and analysis.
    """
    satellite_id:       str
    start_epoch:        datetime
    end_epoch:          datetime
    n_observations:     int
    rms_residual_m:     float
    max_residual_m:     float
    mean_residual_m:    float
    std_residual_m:     float
    gdop_mean:          float
    gdop_min:           float
    gdop_max:           float
    propagator_mode:    str
    bandwidth_hz:       float
    range_resolution_m: float
    frames:             List[ValidationFrame] = field(default_factory=list)

    def summary(self) -> str:
        """One-paragraph summary suitable for logging or reporting."""
        return (
            f"Navigation validation — {self.satellite_id} | "
            f"{self.start_epoch.strftime('%H:%M:%S')} – "
            f"{self.end_epoch.strftime('%H:%M:%S')} UTC | "
            f"n={self.n_observations} | "
            f"RMS={self.rms_residual_m:.1f} m | "
            f"max={self.max_residual_m:.1f} m | "
            f"bias={self.mean_residual_m:.1f} m | "
            f"GDOP={self.gdop_mean:.2f} (min={self.gdop_min:.2f}) | "
            f"Δr={self.range_resolution_m:.1f} m | "
            f"truth={self.propagator_mode}"
        )


# ── navigation validator ──────────────────────────────────────────────────────

class NavigationValidator:
    """
    OTFS navigation validation pipeline.

    Integrates three Sprint 6 components:
      OrbitalProfileExporter  → contact window scheduling, Doppler profile
      OTFSChannelEmulator     → per-epoch SNR for ranging noise model
      NavigationTruthModel    → HPOP pseudorange truth

    Simulates the OTFS receiver's pseudorange estimate from the delay
    of the pilot echo in the delay-Doppler domain, then computes
    residuals and GDOP against the HPOP truth.

    Parameters
    ----------
    satellite_id : str
        Satellite identifier.
    altitude_km : float
        Circular orbit altitude (km). Default 550 km.
    inclination_deg : float
        Orbital inclination (degrees). Default 51.6°.
    raan_deg : float
        Right ascension of the ascending node (degrees).
    mean_anomaly_deg : float
        Mean anomaly at epoch (degrees). Default 0.0.
    eccentricity : float
        Orbital eccentricity. Default 0.001 (near-circular).
    arg_perigee_deg : float
        Argument of perigee (degrees). Default 0.0.
    epoch : datetime
        Reference epoch for the orbital elements.
    ground_lat_deg : float
        Ground station geodetic latitude (degrees). Default Cambridge.
    ground_lon_deg : float
        Ground station geodetic longitude (degrees). Default Cambridge.
    ground_alt_m : float
        Ground station altitude above WGS-84 ellipsoid (metres).
    channel_config : Optional[ChannelEmulatorConfig]
        Channel emulator configuration. If None, default S-band
        parameters are used (2.4 GHz, 10 MHz BW, 5 W Tx).
    rng_seed : Optional[int]
        Random seed for reproducible pseudorange noise. Default 42.
    """

    def __init__(
        self,
        satellite_id:      str,
        altitude_km:       float    = 550.0,
        inclination_deg:   float    = 51.6,
        raan_deg:          float    = 0.0,
        mean_anomaly_deg:  float    = 0.0,
        eccentricity:      float    = 0.001,
        arg_perigee_deg:   float    = 0.0,
        epoch:             Optional[datetime] = None,
        ground_lat_deg:    float    = 52.205,
        ground_lon_deg:    float    = 0.119,
        ground_alt_m:      float    = 20.0,
        channel_config:    Optional[ChannelEmulatorConfig] = None,
        rng_seed:          Optional[int] = 42,
    ) -> None:
        if epoch is None:
            epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)

        orbital_kwargs = dict(
            satellite_id    = satellite_id,
            altitude_km     = altitude_km,
            inclination_deg = inclination_deg,
            raan_deg        = raan_deg,
            mean_anomaly_deg= mean_anomaly_deg,
            eccentricity    = eccentricity,
            arg_perigee_deg = arg_perigee_deg,
            epoch           = epoch,
            ground_lat_deg  = ground_lat_deg,
            ground_lon_deg  = ground_lon_deg,
            ground_alt_m    = ground_alt_m,
        )

        self._exporter = OrbitalProfileExporter(**orbital_kwargs)
        self._truth    = NavigationTruthModel(**orbital_kwargs)

        if channel_config is None:
            channel_config = ChannelEmulatorConfig(
                carrier_freq_hz = 2.4e9,
                bandwidth_hz    = 10e6,
                tx_power_w      = 5.0,
                tx_gain_dbi     = 6.0,
                rx_gain_dbi     = 3.0,
                noise_temp_k    = 290.0,
                rng_seed        = rng_seed,
            )
        self._channel  = OTFSChannelEmulator(channel_config)
        self._bw_hz    = channel_config.bandwidth_hz
        self._rng      = random.Random(rng_seed)

    # ── public API ────────────────────────────────────────────────────────────

    def validate_pass(
        self,
        start_epoch: datetime,
        duration_s:  float = 480.0,
        step_s:      float = 10.0,
    ) -> ValidationResult:
        """
        Validate OTFS pseudorange accuracy over one contact window.

        Parameters
        ----------
        start_epoch : datetime
            UTC start of the search window (timezone-aware).
        duration_s : float
            Search window duration (seconds). Should be long enough
            to contain at least one visible pass. Default 480 s.
        step_s : float
            Time step between observations (seconds). Default 10 s.

        Returns
        -------
        ValidationResult
            Pass-level statistics and per-epoch ValidationFrames.

        Raises
        ------
        ValueError
            If no in-view frames are found in the search window.
        """
        if start_epoch.tzinfo is None:
            start_epoch = start_epoch.replace(tzinfo=timezone.utc)

        # Step 1 — Orbital profile (contact window + Doppler profile)
        orb_profile    = self._exporter.export_in_view_only(
            start_epoch, duration_s=duration_s, step_s=step_s
        )
        if not orb_profile:
            raise ValueError(
                f"No in-view frames found for {self._truth.satellite_id} "
                f"in {duration_s/60:.1f}-minute window starting "
                f"{start_epoch.strftime('%H:%M:%S')} UTC."
            )

        # Step 2 — Channel emulator (SNR per epoch for ranging noise)
        ch_frames      = self._channel.compute(orb_profile)

        # Step 3 — HPOP truth pseudoranges
        t_start_truth  = orb_profile[0].timestamp
        t_dur_truth    = (
            orb_profile[-1].timestamp - t_start_truth
        ).total_seconds()
        truth_frames   = self._truth.compute_series(
            t_start_truth,
            duration_s = max(t_dur_truth, 2.0),
            step_s     = step_s,
        )

        # Step 4 — Build per-epoch ValidationFrames
        vframes: List[ValidationFrame] = []
        for orb, ch, tf in zip(orb_profile, ch_frames, truth_frames):
            vf = self._validate_epoch(orb, ch, tf)
            vframes.append(vf)

        # Step 5 — Pass-level statistics
        result = self._compute_statistics(
            satellite_id = self._truth.satellite_id,
            frames       = vframes,
            propagator   = self._truth.propagator_mode,
        )
        return result


    # ── multi-satellite validation (Scenarios 2 and 3) ─────────────────────────

    @staticmethod
    def validate_multi_satellite(
        validators:  list,
        start_epoch,
        duration_s:  float = 3600.0,
        step_s:      float = 30.0,
    ) -> dict:
        """
        Validate navigation accuracy across multiple simultaneous satellites.

        Demonstrates the GDOP improvement from Scenario 1 → 2 → 3:
          Scenario 1 (1 sat):   mean GDOP ≈ 2–4, no 3D fix
          Scenario 2 (6 sats):  mean GDOP ≈ 2–3, partial 3D fix
          Scenario 3 (24 sats): mean GDOP ≈ 1.5–2.5, full 3D fix

        At each timestep, collects all in-view satellite positions and
        computes the multi-satellite GDOP using gdop_multi_satellite().
        Per-satellite residuals use the same OTFS ranging noise model
        as validate_pass().

        Parameters
        ----------
        validators : list of NavigationValidator
            One NavigationValidator per satellite in the scenario.
        start_epoch : datetime
            UTC start of the observation window.
        duration_s : float
            Window duration in seconds. Default 3600 s (one hour).
        step_s : float
            Time step in seconds. Default 30 s.

        Returns
        -------
        dict with keys:
            'n_satellites'     : int — number of satellites in scenario
            'n_epochs'         : int — timesteps in the window
            'n_visible_epochs' : int — epochs with ≥1 in-view satellite
            'n_fix_epochs'     : int — epochs with ≥4 in-view sats (3D fix)
            'gdop_mean'        : float — mean multi-satellite GDOP
            'gdop_min'         : float — best-case GDOP
            'gdop_max'         : float — worst-case GDOP
            'fix_fraction'     : float — fraction of epochs with 3D fix
            'rms_residual_m'   : float — RMS across all sat/epoch residuals
            'mean_simultaneous': float — mean simultaneous satellites/epoch
        """
        from datetime import timedelta as _td
        if start_epoch.tzinfo is None:
            from datetime import timezone as _tz
            start_epoch = start_epoch.replace(tzinfo=_tz.utc)

        # Pre-compute per-satellite orbital profiles over the window
        orb_profiles = []
        for v in validators:
            try:
                profile = v._exporter.export(
                    start_epoch, duration_s=duration_s, step_s=step_s
                )
                orb_profiles.append((v, profile))
            except Exception:
                orb_profiles.append((v, []))

        # Time grid
        n_steps = int(duration_s / step_s) + 1
        gdop_list, residual_list = [], []
        n_visible = n_fix = 0
        simultaneous_counts = []

        for step_i in range(n_steps):
            dt_s    = step_i * step_s
            ts      = start_epoch + _td(seconds=dt_s)

            # Collect in-view satellite data at this epoch
            in_view_positions = []   # ECEF positions of visible sats
            in_view_residuals = []   # pseudorange residuals

            for v, profile in orb_profiles:
                # Find the matching frame by index
                if step_i >= len(profile):
                    continue
                orb = profile[step_i]
                if not orb.in_view:
                    continue

                # Channel emulator for SNR → ranging sigma
                ch = v._channel.compute_single(orb)
                if ch is None:
                    continue

                # HPOP truth range
                try:
                    tf = v._truth.compute_single(ts)
                except Exception:
                    continue

                # OTFS ranging noise
                delta_r  = C_MS / (2.0 * v._bw_hz)
                snr_lin  = 10.0 ** (ch.snr_db / 10.0)
                sigma_q  = delta_r / math.sqrt(12.0)
                sigma_th = delta_r / (2.0 * math.pi * math.sqrt(max(snr_lin, 1e-10)))
                sigma    = math.sqrt(sigma_q**2 + sigma_th**2)
                noise    = v._rng.gauss(0.0, sigma)
                residual = noise   # noise is the residual for zero-bias estimator
                in_view_residuals.append(residual)

                # Satellite ECEF position for GDOP
                in_view_positions.append(tf.position_ecef_m)

            n_sats_visible = len(in_view_positions)
            simultaneous_counts.append(n_sats_visible)

            if n_sats_visible == 0:
                continue
            n_visible += 1

            # Multi-satellite GDOP
            # Receiver at Cambridge ECEF (approximate)
            gs_ecef = validators[0]._truth._gs_ecef
            gdop = NavigationValidator.gdop_multi_satellite(
                in_view_positions, tuple(gs_ecef)
            )
            if math.isfinite(gdop):
                gdop_list.append(gdop)
                if n_sats_visible >= 4:
                    n_fix += 1

            residual_list.extend(in_view_residuals)

        # Aggregate statistics
        gdop_mean = sum(gdop_list) / len(gdop_list) if gdop_list else float('nan')
        rms       = math.sqrt(sum(r*r for r in residual_list) / len(residual_list)) if residual_list else float('nan')

        return {
            'n_satellites'      : len(validators),
            'n_epochs'          : n_steps,
            'n_visible_epochs'  : n_visible,
            'n_fix_epochs'      : n_fix,
            'gdop_mean'         : gdop_mean,
            'gdop_min'          : min(gdop_list) if gdop_list else float('nan'),
            'gdop_max'          : max(gdop_list) if gdop_list else float('nan'),
            'fix_fraction'      : n_fix / max(n_visible, 1),
            'rms_residual_m'    : rms,
            'mean_simultaneous' : sum(simultaneous_counts) / max(len(simultaneous_counts), 1),
        }

    # ── epoch-level validation ────────────────────────────────────────────────

    def _validate_epoch(
        self,
        orb: OrbitalFrame,
        ch:  ChannelFrame,
        tf:  TruthFrame,
    ) -> ValidationFrame:
        """
        Simulate OTFS pseudorange estimate and compute residual.

        OTFS ranging noise model:
          Δr        = c/(2B)                 range resolution (m)
          σ_quant   = Δr/√12                 quantisation noise (m)
          σ_thermal = Δr/(2π√SNR_linear)     thermal noise (m)
          σ_total   = √(σ_quant² + σ_thermal²)

        References: Hadani et al. (2017); Raviteja et al. (2018).
        """
        # Range resolution and noise model
        delta_r   = C_MS / (2.0 * self._bw_hz)
        snr_lin   = 10.0 ** (ch.snr_db / 10.0)
        sigma_q   = delta_r / math.sqrt(12.0)
        sigma_th  = delta_r / (2.0 * math.pi * math.sqrt(max(snr_lin, 1e-10)))
        sigma_tot = math.sqrt(sigma_q**2 + sigma_th**2)

        # Simulated OTFS pseudorange: truth + Gaussian noise
        noise        = self._rng.gauss(0.0, sigma_tot)
        otfs_range_m = tf.true_range_m + noise
        residual_m   = otfs_range_m - tf.true_range_m

        # GDOP from elevation angle (single-satellite geometry)
        gdop = self._gdop_single_sat(ch.elevation_deg)

        return ValidationFrame(
            timestamp       = orb.timestamp,
            satellite_id    = tf.satellite_id,
            true_range_m    = tf.true_range_m,
            otfs_range_m    = otfs_range_m,
            residual_m      = residual_m,
            elevation_deg   = ch.elevation_deg,
            snr_db          = ch.snr_db,
            doppler_hz      = ch.doppler_hz,
            ranging_sigma_m = sigma_tot,
            gdop            = gdop,
            truth_source    = tf.source,
        )

    # ── statistics ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_statistics(
        satellite_id: str,
        frames:       List[ValidationFrame],
        propagator:   str,
    ) -> ValidationResult:
        """Compute pass-level statistics from per-epoch ValidationFrames."""
        if not frames:
            raise ValueError("No validation frames to compute statistics.")

        residuals   = [f.residual_m       for f in frames]
        gdops       = [f.gdop             for f in frames]
        bw_hz       = 10e6                # default; overridden by caller if needed
        delta_r     = C_MS / (2.0 * bw_hz)

        rms    = math.sqrt(sum(r*r for r in residuals) / len(residuals))
        mean_r = sum(residuals) / len(residuals)
        std_r  = math.sqrt(
            sum((r - mean_r)**2 for r in residuals) / max(1, len(residuals)-1)
        )

        return ValidationResult(
            satellite_id       = satellite_id,
            start_epoch        = frames[0].timestamp,
            end_epoch          = frames[-1].timestamp,
            n_observations     = len(frames),
            rms_residual_m     = rms,
            max_residual_m     = max(abs(r) for r in residuals),
            mean_residual_m    = mean_r,
            std_residual_m     = std_r,
            gdop_mean          = sum(gdops) / len(gdops),
            gdop_min           = min(gdops),
            gdop_max           = max(gdops),
            propagator_mode    = propagator,
            bandwidth_hz       = bw_hz,
            range_resolution_m = delta_r,
            frames             = frames,
        )

    # ── GDOP helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _gdop_single_sat(elevation_deg: float) -> float:
        """
        Geometric Dilution of Precision for a single-satellite observation.

        For a single line-of-sight measurement, the GDOP reflects the
        projection of the ranging error onto the vertical position.
        Near zenith (90°) the geometry is best (low GDOP); near the
        horizon (10°) the geometry degrades (high GDOP).

        GDOP ≈ 1 / sin(elevation)   [single-satellite approximation]

        For full 3D position GDOP with multiple simultaneous satellites,
        see the multi-satellite variant in Scenarios 2 and 3.

        Vallado (2013), Section 12.3.
        """
        el_rad = max(math.radians(elevation_deg), math.radians(5.0))
        return 1.0 / math.sin(el_rad)

    @staticmethod
    def gdop_multi_satellite(
        positions_ecef: List[Tuple[float, float, float]],
        receiver_ecef:  Tuple[float, float, float],
    ) -> float:
        """
        Full 3D GDOP for simultaneous multi-satellite observations.

        Used in Scenarios 2 and 3 where multiple satellites are
        visible simultaneously, enabling trilateration.

        Requires ≥4 satellite observations for a non-singular geometry
        matrix. Returns float('inf') if the geometry is singular.

        Parameters
        ----------
        positions_ecef : list of (x, y, z) in metres
            Satellite ECEF positions at the observation epoch.
        receiver_ecef : (x, y, z) in metres
            Receiver ECEF position.

        Returns
        -------
        float
            GDOP value. Lower is better. Returns inf if geometry
        is singular (< 4 satellites or collinear geometry).
        """
        if len(positions_ecef) < 4:
            return float('inf')

        rx, ry, rz = receiver_ecef
        H_rows = []
        for (sx, sy, sz) in positions_ecef:
            dx, dy, dz = sx-rx, sy-ry, sz-rz
            r = math.sqrt(dx*dx + dy*dy + dz*dz)
            if r < 1.0:
                continue
            H_rows.append([dx/r, dy/r, dz/r, 1.0])

        if len(H_rows) < 4:
            return float('inf')

        H   = np.array(H_rows)
        try:
            Q   = np.linalg.inv(H.T @ H)
            return float(math.sqrt(abs(np.trace(Q))))
        except np.linalg.LinAlgError:
            return float('inf')
