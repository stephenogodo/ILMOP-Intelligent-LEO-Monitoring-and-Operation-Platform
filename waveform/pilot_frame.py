"""
waveform/pilot_frame.py

Three-Function OTFS-ISAC Pilot Frame  (Contribution 1)
=========================================================

Defines the pilot frame structure that enables OTFS to simultaneously
perform three functions — communication, navigation, and remote sensing —
from a single transmitted delay-Doppler frame.

Frame anatomy
-------------
The N×M delay-Doppler grid is partitioned into three non-overlapping regions:

    ┌──────────────────────────── M Doppler bins ────────────────────────────┐
    │                                                                         │
    │  DATA   │      GUARD (Doppler−)       │ PILOT │  GUARD (Doppler+)  │   │ N
    │  DATA   │                             │       │                    │   │ d
    │  DATA   │  GUARD (delay+, Doppler−)   │ GUARD │  GUARD (d+, Dop+) │   │ e
    │         │                             │       │                    │   │ l
    │  DATA   │      DATA                   │  DATA │    DATA            │   │ a
    │         │                             │       │                    │   │ y
    └─────────────────────────────────────────────────────────────────────────┘

    1. PILOT  — single symbol at (k_p, l_p) carrying the known pilot value.
                The pilot power is boosted relative to data to ensure reliable
                echo detection for navigation and channel estimation.

    2. GUARD  — zero-padded region surrounding the pilot.  Its extent in the
                delay direction (k_guard_pos bins) must be ≥ the maximum
                channel delay spread so that the pilot echo falls within the
                guard and does not interfere with data.  Its extent in the
                Doppler direction (l_guard bins each side) must be ≥ the
                maximum Doppler spread of the channel.

    3. DATA   — all remaining bins carry QAM/PSK communication symbols.
                The navigation receiver reads the pilot echo from inside the
                guard region (the echo moves relative to the pilot position).
                The sensing receiver reads target echoes from the guard region
                at positions other than the direct path.

Three-function operation
-------------------------
    Communication:  data symbols in the DATA region are decoded by the
                    receiver after channel equalisation using the channel
                    estimate extracted from the pilot echo.

    Navigation:     the pilot echo in the received Y_DD grid appears at
                    (k_p + k_channel, l_p + l_channel).  The delay offset
                    k_channel gives the pseudorange:
                        ρ̂ = k_channel × Δr = k_channel × c/(2B)

    Remote sensing: target echoes appear at positions other than the direct
                    navigation path in the guard region.  Targets at known
                    RCS values at predetermined positions can be separated
                    from the navigation echo by their distinct (k, l) offsets.

Pilot placement strategy
-------------------------
    The pilot is placed at:
        k_p = k_guard_pos + 1       (leave room for positive-delay echoes above)
        l_p = M // 2                (centre of Doppler axis, zero-Doppler nominal)

    This ensures:
        - Positive channel delays (all physical delays) fall within the guard
          region above the pilot (lower k values in the received grid).
        - Positive and negative Doppler shifts fall within the guard symmetric
          around l_p.
        - The data region is contiguous and as large as possible.

Guard region sizing
--------------------
    Minimum guard sizes for a LEO satellite channel at 550 km, S-band:

        k_guard_pos (delay, bins)  ≥ ceil(2 × range_max / (c × Δτ))
                                  ≥ ceil(2 × range_max × B / c)

        l_guard (Doppler, bins)   ≥ ceil(ν_max / Δν)
                                  = ceil(v_orb × (R_E/R_s) × f_c / (c × Δν))
                                  ≈ ±62 kHz / 610 Hz ≈ ±102 bins at B=10 MHz, N=M=128

    A FixedGuardRegion uses ν_max (horizon Doppler) for l_guard throughout
    the pass.  An AdaptiveGuardRegion (guard_region.py) shrinks l_guard to
    ν(θ) = ν_max × cos(θ) at the current elevation angle θ, recovering
    spectral efficiency near zenith.

Spectral efficiency
--------------------
    η_data = N_data / (N × M)
           = (N×M - N_guard - 1) / (N×M)

    where N_guard = (2×k_guard_pos + 1) × (2×l_guard + 1) - 1  (guard minus pilot)

    For k_guard_pos = 10, l_guard = 105, N = M = 128:
        N_guard = 21 × 211 = 4431
        η_data  = (16384 - 4431) / 16384 ≈ 72.9%

    The AdaptiveGuardRegion reduces l_guard near zenith (ν → 0), increasing
    η_data toward ~98% at zenith (k_guard_pos = 10 only).

References
----------
- Raviteja, P., Viterbo, E., & Hong, Y. (2018a). OTFS performance on
  static multipath channels. IEEE Wireless Communications Letters, 8(3),
  745–748.
- Raviteja, P., Hong, Y., Viterbo, E., & Biglieri, E. (2018b). Practical
  pulse-shaping waveforms for reduced-cyclic-prefix OTFS. IEEE TVT, 68(1).
- Hadani, R., et al. (2017). Orthogonal time frequency space modulation.
  IEEE WCNC 2017.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

from waveform.otfs_signal import OTFSGrid

# ── Physical constants ────────────────────────────────────────────────────────
C_MS = 299_792_458.0


# ═══════════════════════════════════════════════════════════════════════════════
# Pilot frame configuration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PilotFrameConfig:
    """
    Configuration for the three-function OTFS-ISAC pilot frame.

    Parameters
    ----------
    k_guard_pos : int
        Guard region extent in the positive delay direction (bins above the
        pilot, i.e. at lower k values where echoes appear).
        Must be ≥ maximum channel delay in bins.
        Default: 10 bins (covers max range ≈ 10 × Δr ≈ 150 m at B=10 MHz).
    k_guard_neg : int
        Guard region extent in the negative delay direction.
        Physical channels have non-negative delays, so this is typically 0.
        Default: 0.
    l_guard : int
        Guard region half-width in the Doppler direction (symmetric ± around
        the pilot).  Must be ≥ maximum channel Doppler in bins.
        Default: 10 bins (override with FixedGuardRegion or AdaptiveGuardRegion).
    pilot_power_db : float
        Pilot power relative to data symbol average power (dB).
        Boosted pilot power improves echo detection SNR at the cost of a
        small power imbalance in the frame.  Default: 6 dB.
    pilot_value : complex
        The known pilot symbol value (before power boost).  Default: 1+0j.
        ZC sequences (zc_pilot.py) substitute a better pilot here.
    k_pilot_offset : int, optional
        Manual override for the pilot's delay position.  If None, the pilot
        is placed at k_guard_pos + 1 (minimum safe position).
    l_pilot_offset : int, optional
        Manual override for the pilot's Doppler position.  If None, the pilot
        is placed at M // 2 (centre of the Doppler axis).
    """

    k_guard_pos:      int     = 10
    k_guard_neg:      int     = 0
    l_guard:          int     = 10
    pilot_power_db:   float   = 6.0
    pilot_value:      complex = 1.0 + 0j
    k_pilot_offset:   Optional[int] = None
    l_pilot_offset:   Optional[int] = None

    @property
    def pilot_power_linear(self) -> float:
        """Linear pilot power relative to unit data power."""
        return 10.0 ** (self.pilot_power_db / 10.0)

    @property
    def guard_width_delay(self) -> int:
        """Total guard extent in delay direction (neg + pilot + pos)."""
        return self.k_guard_neg + 1 + self.k_guard_pos

    @property
    def guard_width_doppler(self) -> int:
        """Total guard extent in Doppler direction (2 × l_guard + pilot)."""
        return 2 * self.l_guard + 1


# ═══════════════════════════════════════════════════════════════════════════════
# Region masks
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FrameRegions:
    """
    Boolean masks identifying the three regions of a pilot frame.

    All masks have shape (N, M) — True where the region applies.

    Attributes
    ----------
    pilot_mask  : (N, M) bool — True at the single pilot bin.
    guard_mask  : (N, M) bool — True in the guard region (excluding pilot).
    data_mask   : (N, M) bool — True in the data region.
    n_pilot     : int — always 1.
    n_guard     : int — number of guard bins (excluding pilot).
    n_data      : int — number of data bins.
    pilot_k     : int — delay bin of the pilot symbol.
    pilot_l     : int — Doppler bin of the pilot symbol.
    """

    pilot_mask: np.ndarray
    guard_mask: np.ndarray
    data_mask:  np.ndarray
    pilot_k:    int
    pilot_l:    int

    @property
    def n_pilot(self) -> int:
        return int(self.pilot_mask.sum())

    @property
    def n_guard(self) -> int:
        return int(self.guard_mask.sum())

    @property
    def n_data(self) -> int:
        return int(self.data_mask.sum())

    @property
    def n_total(self) -> int:
        return self.n_pilot + self.n_guard + self.n_data

    @property
    def data_fraction(self) -> float:
        """Fraction of the frame carrying data (spectral efficiency proxy)."""
        return self.n_data / self.n_total

    def __repr__(self) -> str:
        N, M = self.pilot_mask.shape
        return (
            f"FrameRegions(N={N}, M={M}, "
            f"pilot=({self.pilot_k},{self.pilot_l}), "
            f"n_data={self.n_data}, n_guard={self.n_guard}, "
            f"η_data={self.data_fraction:.1%})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Pilot frame builder
# ═══════════════════════════════════════════════════════════════════════════════

class PilotFrame:
    """
    Three-function OTFS-ISAC pilot frame builder and parser.

    Responsibilities
    ----------------
    1. Compute the three region masks (pilot, guard, data) for a given
       grid and pilot frame configuration.
    2. Build a transmit delay-Doppler frame X_DD from a data symbol array.
    3. Extract received data symbols from a received Y_DD frame.

    Pilot placement
    ---------------
    By default the pilot is placed at:
        k_pilot = config.k_guard_pos + 1
        l_pilot = M // 2

    This ensures:
        - All positive-delay echoes (0 ≤ k ≤ k_guard_pos) fall inside the
          guard region and are isolated from data symbols.
        - The guard is symmetric in the Doppler direction (±l_guard bins).

    Parameters
    ----------
    grid   : OTFSGrid
        Grid parameters (N, M, bandwidth).
    config : PilotFrameConfig
        Guard region sizes, pilot power, pilot value.
    """

    def __init__(self, grid: OTFSGrid, config: PilotFrameConfig) -> None:
        self.grid   = grid
        self.config = config
        self._validate()

        # Compute pilot position
        self._k_pilot = (
            config.k_pilot_offset
            if config.k_pilot_offset is not None
            else config.k_guard_pos + 1
        )
        self._l_pilot = (
            config.l_pilot_offset
            if config.l_pilot_offset is not None
            else grid.M // 2
        )

        # Build region masks once — reused by build_frame() and extract_data()
        self._regions = self._compute_regions()

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def regions(self) -> FrameRegions:
        """The three region masks for this grid/config combination."""
        return self._regions

    @property
    def pilot_k(self) -> int:
        """Delay bin index of the pilot symbol."""
        return self._k_pilot

    @property
    def pilot_l(self) -> int:
        """Doppler bin index of the pilot symbol."""
        return self._l_pilot

    @property
    def n_data_symbols(self) -> int:
        """Number of data symbols per frame."""
        return self._regions.n_data

    # ── Frame construction ────────────────────────────────────────────────────

    def build_frame(
        self,
        data_symbols: np.ndarray,
        pilot_value:  Optional[complex] = None,
    ) -> np.ndarray:
        """
        Build a transmit delay-Doppler frame.

        The frame contains:
          - The pilot symbol (boosted by pilot_power_db) at (k_pilot, l_pilot).
          - Zeros in the guard region.
          - The provided data_symbols in the data region (row-major order).

        Parameters
        ----------
        data_symbols : np.ndarray, shape (n_data_symbols,), complex
            Communication symbols to place in the data region.
            Length must equal self.n_data_symbols.
        pilot_value : complex, optional
            Override the pilot value from config.  Useful for ZC pilot
            substitution (zc_pilot.py).  If None, uses config.pilot_value.

        Returns
        -------
        X_dd : np.ndarray, shape (N, M), complex
            Transmit delay-Doppler frame.

        Raises
        ------
        ValueError
            If len(data_symbols) ≠ n_data_symbols.
        """
        if data_symbols.shape[0] != self._regions.n_data:
            raise ValueError(
                f"data_symbols length {data_symbols.shape[0]} ≠ "
                f"n_data_symbols {self._regions.n_data}"
            )

        X_dd = np.zeros((self.grid.N, self.grid.M), dtype=complex)

        # 1. Place data symbols in the data region (row-major)
        X_dd[self._regions.data_mask] = data_symbols

        # 2. Place pilot (zero in guard — already zero)
        pv = pilot_value if pilot_value is not None else self.config.pilot_value
        X_dd[self._k_pilot, self._l_pilot] = (
            pv * math.sqrt(self.config.pilot_power_linear)
        )

        return X_dd

    def extract_data(self, Y_dd: np.ndarray) -> np.ndarray:
        """
        Extract data symbols from a received delay-Doppler frame.

        Returns the received signal at data bins in the same row-major
        order used by build_frame().  The caller is responsible for
        equalisation before passing Y_dd here.

        Parameters
        ----------
        Y_dd : np.ndarray, shape (N, M), complex
            Received (and equalised) delay-Doppler frame.

        Returns
        -------
        data_rx : np.ndarray, shape (n_data_symbols,), complex
            Received data symbols.
        """
        return Y_dd[self._regions.data_mask]

    def extract_guard_region(self, Y_dd: np.ndarray) -> Tuple[np.ndarray, int, int]:
        """
        Extract the guard region from a received frame for echo analysis.

        The guard region contains the pilot echo (navigation function) and
        any target echoes (sensing function).

        Parameters
        ----------
        Y_dd : np.ndarray, shape (N, M), complex

        Returns
        -------
        (guard_values, k_start, l_start) : tuple
            guard_values : (k_guard_pos+k_guard_neg+1, 2*l_guard+1) complex array
                The guard region values, including the pilot bin.
            k_start : int
                Delay bin index of the first guard row (= k_pilot - k_guard_neg).
            l_start : int
                Doppler bin index of the first guard column (= l_pilot - l_guard).
        """
        k_start = self._k_pilot - self.config.k_guard_neg
        k_end   = self._k_pilot + self.config.k_guard_pos + 1
        l_start = (self._l_pilot - self.config.l_guard) % self.grid.M
        l_end   = (self._l_pilot + self.config.l_guard + 1) % self.grid.M

        # Handle circular wrap in Doppler direction
        if l_start < l_end:
            guard = Y_dd[k_start:k_end, l_start:l_end]
        else:
            # Doppler axis wraps around
            left  = Y_dd[k_start:k_end, l_start:]
            right = Y_dd[k_start:k_end, :l_end]
            guard = np.concatenate([left, right], axis=1)

        return guard, k_start, l_start

    # ── Spectral efficiency ───────────────────────────────────────────────────

    def spectral_efficiency_fraction(self) -> float:
        """
        Fraction of the N×M frame carrying data symbols.

        η = n_data / (N × M) = 1 - (n_guard + n_pilot) / (N × M)

        This is the conventional spectral efficiency proxy (bits per bin).
        The η_ISAC metric (metrics/isac_efficiency.py) improves on this by
        crediting the pilot and guard overhead for their navigation and
        sensing contributions.
        """
        return self._regions.data_fraction

    def guard_overhead_fraction(self) -> float:
        """Fraction of the frame consumed by pilot + guard."""
        return (self._regions.n_guard + 1) / self._regions.n_total

    # ── Region computation ────────────────────────────────────────────────────

    def _compute_regions(self) -> FrameRegions:
        """
        Compute the three region masks.

        Guard region definition (all indices modulo N, M):
            delay   : k ∈ [k_pilot - k_guard_neg,  k_pilot + k_guard_pos]
            Doppler : l ∈ [l_pilot - l_guard,       l_pilot + l_guard]    (mod M)

        Pilot region: the single bin (k_pilot, l_pilot).
        Data region: everything not in guard or pilot.

        Returns
        -------
        FrameRegions with pilot_mask, guard_mask, data_mask of shape (N, M).
        """
        N, M = self.grid.N, self.grid.M
        cfg  = self.config
        kp, lp = self._k_pilot, self._l_pilot

        # Build the combined pilot+guard mask first, then split
        guard_full = np.zeros((N, M), dtype=bool)

        for dk in range(-cfg.k_guard_neg, cfg.k_guard_pos + 1):
            k = (kp + dk) % N
            for dl in range(-cfg.l_guard, cfg.l_guard + 1):
                l = (lp + dl) % M
                guard_full[k, l] = True

        # Pilot mask: single bin
        pilot_mask = np.zeros((N, M), dtype=bool)
        pilot_mask[kp, lp] = True

        # Guard mask: guard region minus the pilot
        guard_mask = guard_full & ~pilot_mask

        # Data mask: everything else
        data_mask = ~guard_full

        return FrameRegions(
            pilot_mask = pilot_mask,
            guard_mask = guard_mask,
            data_mask  = data_mask,
            pilot_k    = kp,
            pilot_l    = lp,
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self) -> None:
        """
        Validate that the guard region fits within the grid and leaves
        at least one data bin.
        """
        N, M = self.grid.N, self.grid.M
        cfg  = self.config

        # Pilot position
        k_p = cfg.k_pilot_offset if cfg.k_pilot_offset is not None \
              else cfg.k_guard_pos + 1
        l_p = cfg.l_pilot_offset if cfg.l_pilot_offset is not None \
              else M // 2

        # Guard extent in delay
        k_guard_total = cfg.k_guard_neg + 1 + cfg.k_guard_pos
        if k_guard_total > N:
            raise ValueError(
                f"Guard region height {k_guard_total} exceeds N={N}. "
                f"Reduce k_guard_pos ({cfg.k_guard_pos}) or k_guard_neg ({cfg.k_guard_neg})."
            )

        # Guard extent in Doppler
        l_guard_total = 2 * cfg.l_guard + 1
        if l_guard_total > M:
            raise ValueError(
                f"Guard region width {l_guard_total} exceeds M={M}. "
                f"Reduce l_guard ({cfg.l_guard})."
            )

        # Pilot position must be reachable given guard in delay direction
        if k_p < cfg.k_guard_neg:
            raise ValueError(
                f"Pilot delay bin k_p={k_p} < k_guard_neg={cfg.k_guard_neg}. "
                f"Guard region would extend to negative delay bins (not physical). "
                f"Increase k_pilot_offset or decrease k_guard_neg."
            )
        if k_p + cfg.k_guard_pos >= N:
            raise ValueError(
                f"Pilot delay bin k_p={k_p} + k_guard_pos={cfg.k_guard_pos} "
                f"= {k_p + cfg.k_guard_pos} ≥ N={N}. "
                f"Pilot is too close to the end of the delay axis."
            )

        # At least some data bins must remain
        guard_total = k_guard_total * l_guard_total
        if guard_total >= N * M:
            raise ValueError(
                f"Guard region ({guard_total} bins) fills the entire frame "
                f"({N*M} bins). Reduce guard sizes."
            )

    # ── Representation ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        r = self._regions
        return (
            f"PilotFrame(\n"
            f"  grid  = {self.grid},\n"
            f"  pilot = ({self._k_pilot}, {self._l_pilot}),\n"
            f"  guard = ±{self.config.k_guard_pos} delay × "
            f"±{self.config.l_guard} Doppler bins,\n"
            f"  {r.n_data} data bins ({r.data_fraction:.1%} of frame),\n"
            f"  {r.n_guard + 1} pilot+guard bins "
            f"({self.guard_overhead_fraction():.1%} overhead)\n"
            f")"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Factory helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_leo_pilot_frame(
    grid:                       OTFSGrid,
    max_target_range_offset_m:  float = 200.0,
    max_doppler_hz:             float = 62e3,
    pilot_power_db:             float = 6.0,
) -> PilotFrame:
    """
    Build a PilotFrame sized for a LEO satellite ISAC channel.

    The guard region covers:
    - Delay direction: the maximum *range offset* of target echoes relative
      to the direct-path pilot echo (not the absolute satellite range).
      Physical delays are non-negative, so k_guard_neg = 0.
    - Doppler direction: the maximum Doppler shift of the satellite channel.

    Parameters
    ----------
    grid : OTFSGrid
        Grid parameters.
    max_target_range_offset_m : float
        Maximum range offset of a sensing target from the direct path (m).
        Sets k_guard_pos.  Default 200 m (suitable for short-range targets).
        For the navigation function only (no targets), this can be 1–5 bins.
    max_doppler_hz : float
        Maximum Doppler shift magnitude (Hz).  Sets l_guard.
        Default 62 kHz (S-band, 550 km orbit, horizon pass).
    pilot_power_db : float
        Pilot power boost in dB.  Default 6 dB.

    Returns
    -------
    PilotFrame sized for the given channel parameters.

    Notes
    -----
    The absolute satellite-to-ground propagation delay (~1.8 ms at 550 km)
    is handled by synchronisation — the receiver aligns to the expected
    arrival time.  The guard region only needs to accommodate the
    *differential* channel spread relative to the direct path.
    """
    # Delay guard: bins to cover max round-trip range offset
    # k_guard = ceil(2 * range_offset * B / c)
    k_guard = max(1, math.ceil(
        2.0 * max_target_range_offset_m * grid.bandwidth_hz / C_MS
    ))

    # Doppler guard: bins to cover max Doppler shift
    l_guard = math.ceil(max_doppler_hz / grid.doppler_resolution_hz)

    # Check that the grid is large enough for the requested Doppler guard
    if 2 * l_guard + 1 > grid.M:
        max_n_for_fit = int(grid.bandwidth_hz / (2.0 * max_doppler_hz))
        raise ValueError(
            f"Requested max_doppler_hz={max_doppler_hz/1e3:.0f} kHz requires "
            f"l_guard={l_guard} bins (total guard width {2*l_guard+1}) "
            f"which exceeds M={grid.M}.\n"
            f"The guard fits only when N ≤ B/(2×max_doppler) = "
            f"{max_n_for_fit}.\n"
            f"Use a rectangular grid — e.g. OTFSGrid(N=32, M=256, "
            f"bandwidth_hz={grid.bandwidth_hz:.0f}) gives Δν="
            f"{grid.bandwidth_hz/(32*256):.0f} Hz, "
            f"l_guard={math.ceil(max_doppler_hz/(grid.bandwidth_hz/(32*256)))} "
            f"bins (total {2*math.ceil(max_doppler_hz/(grid.bandwidth_hz/(32*256)))+1} ≤ 256)."
        )

    config = PilotFrameConfig(
        k_guard_pos    = k_guard,
        k_guard_neg    = 0,
        l_guard        = l_guard,
        pilot_power_db = pilot_power_db,
    )
    return PilotFrame(grid, config)


def qpsk_symbols(n: int, rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """
    Generate n random QPSK symbols at unit average power.

    QPSK constellation: (±1 ± j) / √2

    Parameters
    ----------
    n : int
        Number of symbols to generate.
    rng : np.random.Generator, optional
        Random number generator.  Default: np.random.default_rng(42).

    Returns
    -------
    symbols : np.ndarray, shape (n,), complex
        Unit-power QPSK symbols.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    bits = rng.integers(0, 4, size=n)
    mapping = np.array([1+1j, 1-1j, -1+1j, -1-1j]) / math.sqrt(2.0)
    return mapping[bits]
