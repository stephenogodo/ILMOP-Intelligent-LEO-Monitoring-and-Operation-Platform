"""
waveform/guard_region.py

Guard Region Design for OTFS-ISAC LEO Waveforms  (Contribution 2)
===================================================================

Two classes in one module.  The adaptive contribution only has scientific
weight measured against the fixed worst-case baseline — see WDR-006.

FixedGuardRegion
----------------
The baseline.  The Doppler guard is sized to ν_max — the maximum Doppler
shift at the horizon (elevation = 0°) — and held constant throughout the
pass regardless of the satellite's actual elevation angle.

This is the standard approach in the OTFS literature (Raviteja et al.
2018a, 2018b).  It wastes spectral efficiency near zenith where the
actual Doppler shift approaches zero.

AdaptiveGuardRegion  (Contribution 2)
--------------------------------------
The guard is sized to the satellite's instantaneous Doppler shift:

    ν(t) = ṙ(t) · f_c / c

where ṙ(t) is the range rate from the ILMOP SGP4 orbital profile
(OrbitalFrame.range_rate_ms) and f_c is the carrier frequency.

The Doppler shift follows the elevation angle θ(t):

    ν(t) = ν_max · cos(θ(t))

Near zenith (θ → 90°):  ν(t) → 0,  l_guard → safety margin only.
Near horizon (θ → 0°):  ν(t) → ν_max,  l_guard → fixed guard value.

The guard is never smaller than a configurable safety margin δ to absorb
SGP4 range rate prediction error and receiver timing uncertainty.

Spectral efficiency gain
------------------------
The pass-averaged spectral efficiency gain from the adaptive scheme:

    Δη(t) = [l_guard_fixed − l_guard(t)] × 2 / (N × M)

This gain is maximum at zenith (ν(t) → 0) and zero at the horizon.
The pass-averaged Δη is the primary Figure of Merit for Contribution 2,
evaluated in metrics/isac_efficiency.py.

Grid sizing constraint (WDR-005)
----------------------------------
The guard fits within the Doppler axis only when:

    N ≤ B / (2 × ν_max) = B × c / (2 × v_orb × f_c)

At B=10 MHz, S-band (2.4 GHz), h=550 km:  N ≤ 80.
Recommended grid for full LEO Doppler coverage: N=32, M=256.

References
----------
- Raviteja, P., Viterbo, E., & Hong, Y. (2018a). OTFS performance on
  static multipath channels. IEEE Wireless Commun. Lett., 8(3), 745–748.
- Raviteja, P., Hong, Y., Viterbo, E., & Biglieri, E. (2018b). Practical
  pulse-shaping waveforms for reduced-cyclic-prefix OTFS. IEEE TVT, 68(1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from waveform.otfs_signal import OTFSGrid

# ── Physical constants ────────────────────────────────────────────────────────
C_MS      = 299_792_458.0   # speed of light (m/s)
MU        = 3.986004418e14  # Earth gravitational parameter (m³/s²)
R_E_M     = 6_371_000.0     # Earth radius (m)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared result dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GuardResult:
    """
    Guard region size computed for one epoch.

    Attributes
    ----------
    l_guard : int
        Doppler guard half-width in bins (symmetric ± around the pilot).
        Total Doppler guard width = 2 × l_guard + 1.
    k_guard_pos : int
        Delay guard extent in positive direction (bins).
    doppler_hz : float
        Instantaneous Doppler shift used to compute this guard (Hz).
        For FixedGuardRegion, this equals ν_max throughout.
    elevation_deg : float
        Elevation angle at this epoch (degrees).
        NaN for FixedGuardRegion (not elevation-aware).
    guard_overhead_fraction : float
        Fraction of the N×M frame consumed by pilot + guard at this epoch.
    data_fraction : float
        Fraction of the N×M frame available for data symbols at this epoch.
        = 1 − guard_overhead_fraction.
    """
    l_guard:                 int
    k_guard_pos:             int
    doppler_hz:              float
    elevation_deg:           float
    guard_overhead_fraction: float
    data_fraction:           float


@dataclass
class PassEfficiencyResult:
    """
    Pass-averaged spectral efficiency comparison between fixed and adaptive
    guard region schemes.

    Attributes
    ----------
    fixed_eta_mean : float
        Pass-averaged data fraction under the FixedGuardRegion.
    adaptive_eta_mean : float
        Pass-averaged data fraction under the AdaptiveGuardRegion.
    delta_eta_mean : float
        Pass-averaged spectral efficiency gain: adaptive − fixed.
    delta_eta_max : float
        Maximum instantaneous spectral efficiency gain (at zenith).
    n_epochs : int
        Number of epochs (in-view frames) used in the computation.
    epochs_fixed : List[GuardResult]
        Per-epoch results for the fixed guard.
    epochs_adaptive : List[GuardResult]
        Per-epoch results for the adaptive guard.
    """
    fixed_eta_mean:     float
    adaptive_eta_mean:  float
    delta_eta_mean:     float
    delta_eta_max:      float
    n_epochs:           int
    epochs_fixed:       List[GuardResult] = field(default_factory=list)
    epochs_adaptive:    List[GuardResult] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Pass efficiency — {self.n_epochs} epochs | "
            f"fixed η={self.fixed_eta_mean:.1%} | "
            f"adaptive η={self.adaptive_eta_mean:.1%} | "
            f"Δη_mean={self.delta_eta_mean:.1%} | "
            f"Δη_max={self.delta_eta_max:.1%}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Fixed guard region (baseline)
# ═══════════════════════════════════════════════════════════════════════════════

class FixedGuardRegion:
    """
    Baseline guard region sized to the worst-case horizon Doppler ν_max.

    The guard width is constant throughout the satellite pass regardless
    of the actual elevation angle.  This is the standard approach in the
    OTFS literature (Raviteja et al. 2018a, 2018b).

    Parameters
    ----------
    grid : OTFSGrid
        Delay-Doppler grid parameters (N, M, bandwidth_hz).
    nu_max_hz : float
        Maximum Doppler shift in Hz. For a LEO satellite:
            ν_max = v_orb × f_c / c
        At h=550 km, S-band (2.4 GHz): ν_max ≈ 60.8 kHz.
    k_guard_pos : int
        Delay guard extent (positive direction, bins).
        Set to ceil(2 × max_target_range_offset / Δr).
        Default: 10 bins.
    k_guard_neg : int
        Delay guard extent (negative direction, bins).
        Physical delays are non-negative, so default is 0.
    safety_bins : int
        Minimum guard half-width in Doppler bins regardless of ν(t).
        Acts as a floor for l_guard even at zenith.  Default: 2.

    Raises
    ------
    ValueError
        If the computed l_guard exceeds M//2 − 1 (guard cannot fit in
        the Doppler axis).  See WDR-005 for the grid sizing constraint.
    """

    def __init__(
        self,
        grid:        OTFSGrid,
        nu_max_hz:   float,
        k_guard_pos: int   = 10,
        k_guard_neg: int   = 0,
        safety_bins: int   = 2,
    ) -> None:
        self.grid        = grid
        self.nu_max_hz   = nu_max_hz
        self.k_guard_pos = k_guard_pos
        self.k_guard_neg = k_guard_neg
        self.safety_bins = safety_bins

        # Compute fixed l_guard from ν_max
        self._l_guard = max(
            safety_bins,
            math.ceil(nu_max_hz / grid.doppler_resolution_hz),
        )

        # Validate grid capacity (WDR-005)
        if 2 * self._l_guard + 1 > grid.M:
            n_max = int(grid.bandwidth_hz / (2.0 * nu_max_hz))
            raise ValueError(
                f"FixedGuardRegion: l_guard={self._l_guard} bins — total guard "
                f"width {2*self._l_guard+1} exceeds M={grid.M}.\n"
                f"Grid sizing constraint (WDR-005): N ≤ B/(2ν_max) = {n_max}.\n"
                f"Current N={grid.N}. Use a rectangular grid, e.g. "
                f"OTFSGrid(N={min(n_max, 32)}, M={max(grid.M, 256)}, "
                f"bandwidth_hz={grid.bandwidth_hz:.0f})."
            )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def l_guard(self) -> int:
        """Fixed Doppler guard half-width (bins). Constant throughout pass."""
        return self._l_guard

    @property
    def guard_size_bins(self) -> int:
        """Total bins in the guard region (including pilot)."""
        return (self.k_guard_neg + 1 + self.k_guard_pos) * (2 * self._l_guard + 1)

    @property
    def data_fraction(self) -> float:
        """Fraction of the N×M frame available for data. Constant throughout pass."""
        NM = self.grid.N * self.grid.M
        guard_and_pilot = self.guard_size_bins
        return (NM - guard_and_pilot) / NM

    # ── Per-epoch interface ───────────────────────────────────────────────────

    def compute(self, elevation_deg: float = float('nan')) -> GuardResult:
        """
        Return the guard region parameters for one epoch.

        For FixedGuardRegion the result is the same at every epoch.

        Parameters
        ----------
        elevation_deg : float, optional
            Current satellite elevation angle (degrees). Ignored by
            FixedGuardRegion but accepted to maintain the same interface
            as AdaptiveGuardRegion.

        Returns
        -------
        GuardResult with constant l_guard = ceil(ν_max / Δν).
        """
        NM = self.grid.N * self.grid.M
        guard_bins = self.guard_size_bins
        overhead   = guard_bins / NM
        return GuardResult(
            l_guard                 = self._l_guard,
            k_guard_pos             = self.k_guard_pos,
            doppler_hz              = self.nu_max_hz,
            elevation_deg           = elevation_deg,
            guard_overhead_fraction = overhead,
            data_fraction           = 1.0 - overhead,
        )

    def compute_for_pass(
        self,
        elevation_profile_deg: Sequence[float],
    ) -> List[GuardResult]:
        """
        Compute guard parameters for every epoch in a satellite pass.

        Parameters
        ----------
        elevation_profile_deg : sequence of float
            Elevation angle (degrees) at each epoch. For FixedGuardRegion
            all epochs return the same l_guard.

        Returns
        -------
        List of GuardResult, one per in-view epoch.
        """
        return [
            self.compute(el)
            for el in elevation_profile_deg
            if el >= 0.0
        ]

    def __repr__(self) -> str:
        return (
            f"FixedGuardRegion("
            f"ν_max={self.nu_max_hz/1e3:.1f} kHz, "
            f"l_guard={self._l_guard}, "
            f"η_data={self.data_fraction:.1%})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Adaptive guard region (Contribution 2)
# ═══════════════════════════════════════════════════════════════════════════════

class AdaptiveGuardRegion:
    """
    Adaptive guard region sized to the satellite's instantaneous Doppler
    shift ν(t) = ṙ(t) · f_c / c  (Contribution 2).

    The guard width shrinks as the satellite rises above the horizon
    toward zenith where ν(t) → 0. Near zenith the guard reduces to
    the safety margin alone, recovering significant spectral efficiency.

    ν(t) = ν_max · cos(θ(t))

    where θ(t) is the elevation angle from the ILMOP orbital profile.

    Parameters
    ----------
    grid : OTFSGrid
        Delay-Doppler grid parameters.
    carrier_freq_hz : float
        Carrier frequency f_c in Hz.  Used to convert range rate to
        Doppler shift: ν(t) = ṙ(t) · f_c / c.
    altitude_km : float
        Satellite altitude in km (for computing ν_max from orbital mechanics).
        Default 550 km.
    k_guard_pos : int
        Delay guard extent (positive direction, bins). Default 10.
    k_guard_neg : int
        Delay guard extent (negative direction, bins). Default 0.
    safety_bins : int
        Minimum guard half-width in Doppler bins. The guard never shrinks
        below this value even at zenith. Acts as a safety margin absorbing
        SGP4 range rate prediction error and receiver timing uncertainty.
        Default 2.

    Notes
    -----
    When an OrbitalFrame is available (from ILMOP OrbitalProfileExporter),
    use compute_from_frame() which reads ṙ(t) directly. When only the
    elevation angle is available, use compute() which derives ν(t) from
    ν(t) = ν_max · cos(θ(t)).

    Raises
    ------
    ValueError
        If ν_max for this orbit/band combination violates the grid sizing
        constraint (WDR-005): N > B/(2ν_max).
    """

    def __init__(
        self,
        grid:            OTFSGrid,
        carrier_freq_hz: float,
        altitude_km:     float  = 550.0,
        k_guard_pos:     int    = 10,
        k_guard_neg:     int    = 0,
        safety_bins:     int    = 2,
    ) -> None:
        self.grid            = grid
        self.carrier_freq_hz = carrier_freq_hz
        self.altitude_km     = altitude_km
        self.k_guard_pos     = k_guard_pos
        self.k_guard_neg     = k_guard_neg
        self.safety_bins     = safety_bins

        # Compute ν_max from orbital mechanics
        r_s          = R_E_M + altitude_km * 1000.0
        self._v_orb  = math.sqrt(MU / r_s)
        self._nu_max = self._v_orb * carrier_freq_hz / C_MS

        # Validate grid capacity at worst case (ν_max)
        l_guard_max = math.ceil(self._nu_max / grid.doppler_resolution_hz)
        if 2 * l_guard_max + 1 > grid.M:
            n_max = int(grid.bandwidth_hz / (2.0 * self._nu_max))
            raise ValueError(
                f"AdaptiveGuardRegion: maximum l_guard={l_guard_max} at horizon — "
                f"total guard width {2*l_guard_max+1} exceeds M={grid.M}.\n"
                f"Grid constraint (WDR-005): N ≤ B/(2ν_max) = {n_max}. "
                f"Current N={grid.N}."
            )

        # Build a fixed reference for comparison
        self._fixed = FixedGuardRegion(
            grid        = grid,
            nu_max_hz   = self._nu_max,
            k_guard_pos = k_guard_pos,
            k_guard_neg = k_guard_neg,
            safety_bins = safety_bins,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def nu_max_hz(self) -> float:
        """Maximum Doppler shift at the horizon (Hz)."""
        return self._nu_max

    @property
    def v_orb_ms(self) -> float:
        """Orbital velocity (m/s)."""
        return self._v_orb

    @property
    def fixed_reference(self) -> FixedGuardRegion:
        """The FixedGuardRegion baseline for comparison at this orbit/band."""
        return self._fixed

    # ── Per-epoch interface ───────────────────────────────────────────────────

    def compute(self, elevation_deg: float) -> GuardResult:
        """
        Compute adaptive guard width from elevation angle.

        Uses the approximate relationship ν(t) = ν_max · cos(θ(t)).
        For more accurate computation when ṙ(t) is available from
        ILMOP, use compute_from_range_rate().

        Parameters
        ----------
        elevation_deg : float
            Current satellite elevation angle in degrees [0, 90].

        Returns
        -------
        GuardResult with l_guard sized to ν(t) + safety margin.
        """
        el_rad     = math.radians(max(0.0, elevation_deg))
        nu_t       = self._nu_max * math.cos(el_rad)
        return self._guard_from_doppler(nu_t, elevation_deg)

    def compute_from_range_rate(
        self,
        range_rate_ms:  float,
        elevation_deg:  float = float('nan'),
    ) -> GuardResult:
        """
        Compute adaptive guard width from the instantaneous range rate.

        This is the primary method when ILMOP orbital profile data is
        available.  The range rate from OrbitalFrame.range_rate_ms gives
        a more accurate Doppler than the elevation-angle approximation.

        Parameters
        ----------
        range_rate_ms : float
            Range rate ṙ(t) in m/s (from OrbitalFrame.range_rate_ms).
            Positive = satellite receding (range increasing).
        elevation_deg : float, optional
            Elevation angle for logging (not used in computation).

        Returns
        -------
        GuardResult with l_guard sized to |ν(t)| + safety margin.
        """
        nu_t = abs(range_rate_ms) * self.carrier_freq_hz / C_MS
        return self._guard_from_doppler(nu_t, elevation_deg)

    def compute_for_pass(
        self,
        elevation_profile_deg: Sequence[float],
    ) -> List[GuardResult]:
        """
        Compute adaptive guard parameters for every epoch in a pass.

        Parameters
        ----------
        elevation_profile_deg : sequence of float
            Elevation angle (degrees) at each epoch. Epochs below 0°
            are skipped (satellite not in view).

        Returns
        -------
        List of GuardResult, one per in-view epoch (elevation ≥ 0°).
        """
        return [
            self.compute(el)
            for el in elevation_profile_deg
            if el >= 0.0
        ]

    def compute_for_pass_from_frames(
        self,
        orbital_frames,
    ) -> List[GuardResult]:
        """
        Compute adaptive guard parameters using ILMOP OrbitalFrame data.

        Uses OrbitalFrame.range_rate_ms for accurate Doppler computation
        instead of the elevation-angle approximation.

        Parameters
        ----------
        orbital_frames : sequence of OrbitalFrame
            Frames from OrbitalProfileExporter.export(). Only in-view
            frames (in_view=True) are processed.

        Returns
        -------
        List of GuardResult, one per in-view frame.
        """
        return [
            self.compute_from_range_rate(
                range_rate_ms = f.range_rate_ms,
                elevation_deg = f.elevation_deg,
            )
            for f in orbital_frames
            if f.in_view
        ]

    # ── Pass-level efficiency analysis ───────────────────────────────────────

    def compare_with_fixed(
        self,
        elevation_profile_deg: Sequence[float],
    ) -> PassEfficiencyResult:
        """
        Compare adaptive vs fixed guard spectral efficiency over a pass.

        This is the primary measurement for Contribution 2.  The result
        quantifies the pass-averaged spectral efficiency gain from the
        adaptive scheme relative to the fixed worst-case baseline.

        Parameters
        ----------
        elevation_profile_deg : sequence of float
            Elevation angle profile for the satellite pass.

        Returns
        -------
        PassEfficiencyResult with pass-averaged efficiency statistics.
        """
        fixed_results    = self._fixed.compute_for_pass(elevation_profile_deg)
        adaptive_results = self.compute_for_pass(elevation_profile_deg)

        if not fixed_results:
            raise ValueError("No in-view epochs in elevation profile.")

        fixed_etas    = [r.data_fraction for r in fixed_results]
        adaptive_etas = [r.data_fraction for r in adaptive_results]
        delta_etas    = [a - f for a, f in zip(adaptive_etas, fixed_etas)]

        return PassEfficiencyResult(
            fixed_eta_mean    = sum(fixed_etas)    / len(fixed_etas),
            adaptive_eta_mean = sum(adaptive_etas) / len(adaptive_etas),
            delta_eta_mean    = sum(delta_etas)    / len(delta_etas),
            delta_eta_max     = max(delta_etas),
            n_epochs          = len(fixed_results),
            epochs_fixed      = fixed_results,
            epochs_adaptive   = adaptive_results,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _guard_from_doppler(
        self,
        nu_t:         float,
        elevation_deg: float,
    ) -> GuardResult:
        """Compute GuardResult from instantaneous Doppler shift ν(t)."""
        l_guard = max(
            self.safety_bins,
            math.ceil(nu_t / self.grid.doppler_resolution_hz),
        )
        NM         = self.grid.N * self.grid.M
        guard_bins = (
            (self.k_guard_neg + 1 + self.k_guard_pos)
            * (2 * l_guard + 1)
        )
        overhead   = guard_bins / NM
        return GuardResult(
            l_guard                 = l_guard,
            k_guard_pos             = self.k_guard_pos,
            doppler_hz              = nu_t,
            elevation_deg           = elevation_deg,
            guard_overhead_fraction = overhead,
            data_fraction           = 1.0 - overhead,
        )

    def __repr__(self) -> str:
        return (
            f"AdaptiveGuardRegion("
            f"f_c={self.carrier_freq_hz/1e9:.2f} GHz, "
            f"h={self.altitude_km:.0f} km, "
            f"ν_max={self._nu_max/1e3:.1f} kHz, "
            f"safety_bins={self.safety_bins})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Factory helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_sband_leo_guards(
    grid:        OTFSGrid,
    altitude_km: float = 550.0,
    k_guard_pos: int   = 10,
    safety_bins: int   = 2,
) -> tuple[FixedGuardRegion, AdaptiveGuardRegion]:
    """
    Build a matched pair of Fixed and Adaptive guard regions for S-band LEO.

    Carrier frequency: 2.4 GHz.
    Recommended grid: OTFSGrid(N=32, M=256, bandwidth_hz=10e6).

    Parameters
    ----------
    grid : OTFSGrid
        Grid parameters. Must satisfy N ≤ 80 at B=10 MHz (WDR-005).
    altitude_km : float
        Orbital altitude in km. Default 550 km.
    k_guard_pos : int
        Delay guard extent in bins. Default 10.
    safety_bins : int
        Minimum Doppler guard half-width (bins). Default 2.

    Returns
    -------
    (FixedGuardRegion, AdaptiveGuardRegion)
        Both sized for the same orbit and band for direct comparison.
    """
    S_BAND_FC = 2.4e9
    r_s       = R_E_M + altitude_km * 1000.0
    v_orb     = math.sqrt(MU / r_s)
    nu_max    = v_orb * S_BAND_FC / C_MS

    fixed = FixedGuardRegion(
        grid        = grid,
        nu_max_hz   = nu_max,
        k_guard_pos = k_guard_pos,
        safety_bins = safety_bins,
    )
    adaptive = AdaptiveGuardRegion(
        grid            = grid,
        carrier_freq_hz = S_BAND_FC,
        altitude_km     = altitude_km,
        k_guard_pos     = k_guard_pos,
        safety_bins     = safety_bins,
    )
    return fixed, adaptive


def nu_max_hz(altitude_km: float, carrier_freq_hz: float) -> float:
    """
    Compute maximum LEO Doppler shift for a given altitude and frequency.

    ν_max = v_orb × f_c / c  where  v_orb = sqrt(μ / (R_E + h))

    Parameters
    ----------
    altitude_km : float
        Circular orbit altitude (km).
    carrier_freq_hz : float
        Carrier frequency (Hz).

    Returns
    -------
    float
        Maximum Doppler shift (Hz).
    """
    r_s   = R_E_M + altitude_km * 1000.0
    v_orb = math.sqrt(MU / r_s)
    return v_orb * carrier_freq_hz / C_MS


def n_max_for_grid(
    bandwidth_hz:    float,
    altitude_km:     float,
    carrier_freq_hz: float,
) -> int:
    """
    Maximum N for the guard region to fit in the Doppler axis (WDR-005).

    N_max = floor( B × c / (2 × v_orb × f_c) )

    Parameters
    ----------
    bandwidth_hz : float
        Signal bandwidth B (Hz).
    altitude_km : float
        Orbital altitude (km).
    carrier_freq_hz : float
        Carrier frequency f_c (Hz).

    Returns
    -------
    int
        Maximum number of delay bins N such that the full Doppler guard
        fits within the Doppler axis of length M.
    """
    nu_max = nu_max_hz(altitude_km, carrier_freq_hz)
    return int(bandwidth_hz / (2.0 * nu_max))
