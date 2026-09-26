"""
waveform/otfs_signal.py

OTFS Signal Model
==================

Implements the core OTFS (Orthogonal Time Frequency Space) modulation
chain for an ILMOP LEO satellite ISAC waveform:

    TX: X_DD[k,l] → ISFFT → X_TF[n,m] → Heisenberg → s[t]
    RX: r[t] → Wigner-Ville → Y_TF[n,m] → SFFT → Y_DD[k,l]

Notation
--------
    N   — number of delay bins  (delay axis, axis=0 of the DD grid)
    M   — number of Doppler bins (Doppler axis, axis=1 of the DD grid)
    B   — bandwidth in Hz        B = N × Δf
    Δf  — subcarrier spacing     Δf = B / N
    T   — OTFS symbol duration   T = 1 / Δf  (one column of X_TF)
    T_f — total frame duration   T_f = M × T = M / Δf

    Delay resolution:   Δτ = 1/B   (seconds)    → range res Δr = c/(2B)
    Doppler resolution: Δν = 1/(M·T) = Δf/M (Hz)

    For B = 10 MHz:  Δr = 14.99 m
    For M = 128, N = 128:  Δν = 78.125 Hz at B = 10 MHz

Axes convention (matches Raviteja et al. 2018a throughout)
----------------------------------------------------------
    X_DD.shape = (N, M)
        axis 0 (rows)    → delay bins   k = 0 … N-1
        axis 1 (columns) → Doppler bins l = 0 … M-1

    X_TF.shape = (N, M)
        axis 0 (rows)    → time slots   n = 0 … N-1
        axis 1 (columns) → sub-carriers m = 0 … M-1

Transforms
----------
    ISFFT (Delay-Doppler → Time-Frequency):
        X_TF[n,m] = Σ_k Σ_l X_DD[k,l] · exp(+j2πnk/N) · exp(-j2πml/M)
                  = IFFT_N{DFT_M{X_DD}}   (IFFT along axis 0 after FFT along axis 1)

    SFFT (Time-Frequency → Delay-Doppler):
        Y_DD[k,l] = (1/NM) Σ_n Σ_m Y_TF[n,m] · exp(-j2πnk/N) · exp(+j2πml/M)
                  = FFT_N{IDFT_M{Y_TF}}   (FFT along axis 0 after IFFT along axis 1)

    Round-trip identity: SFFT(ISFFT(X)) = X  (verified in test suite).

Heisenberg transform (rectangular pulse shaping)
-------------------------------------------------
    The transmitted time-domain signal is formed by treating each time
    slot n as one OFDM-style multi-carrier symbol with M sub-carriers:

        s[n*M + m] = X_TF[n, m]   for n = 0 … N-1, m = 0 … M-1

    This is a flat concatenation of the columns of X_TF.  For rectangular
    pulse shaping this simplifies to elementwise — no windowing needed.
    The full frame is N×M complex samples at sampling rate B.

Wigner-Ville receive filter (rectangular pulse)
-----------------------------------------------
    The inverse operation: reshape the received NM-sample block back to
    (N, M) time-frequency form.

        Y_TF[n, m] = r[n*M + m]

    Followed by SFFT to recover Y_DD.

Channel model in the delay-Doppler domain
-----------------------------------------
    For a single scatterer at delay τ_p (bins k_p) and Doppler ν_p (bins l_p):

        Y_DD[k, l] = h_p · X_DD[(k - k_p) % N, (l - l_p) % M] + noise

    The channel is a 2D circular convolution in the delay-Doppler grid.
    For the LEO ISAC waveform, the pilot echo appears at:
        k_p = round(2 · range_m / (c · Δτ))    (delay index)
        l_p = round(doppler_hz / Δν)            (Doppler index)

References
----------
- Hadani, R., Rakib, S., Tsatsanis, M., et al. (2017). Orthogonal time
  frequency space modulation. IEEE WCNC 2017.
  https://arxiv.org/abs/1808.00519
- Raviteja, P., Viterbo, E., & Hong, Y. (2018a). OTFS performance on
  static multipath channels. IEEE Wireless Communications Letters, 8(3),
  745–748. https://doi.org/10.1109/LWC.2018.2890643
- Raviteja, P., Hong, Y., Viterbo, E., & Biglieri, E. (2018b). Practical
  pulse-shaping waveforms for reduced-cyclic-prefix OTFS. IEEE
  Transactions on Vehicular Technology, 68(1), 957–961.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# ── Physical constants ────────────────────────────────────────────────────────
C_MS = 299_792_458.0   # speed of light (m/s)


# ═══════════════════════════════════════════════════════════════════════════════
# Grid parameters
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OTFSGrid:
    """
    Immutable descriptor for an OTFS delay-Doppler grid.

    Parameters
    ----------
    N : int
        Number of delay bins.  Corresponds to the number of OTFS symbols
        in the time direction (rows of X_DD / X_TF).
        Typical value: 128 or 256.
    M : int
        Number of Doppler bins.  Corresponds to the number of sub-carriers
        in the frequency direction (columns of X_DD / X_TF).
        Typical value: 128 or 256.
    bandwidth_hz : float
        System bandwidth B = N × Δf (Hz).  Default 10 MHz.

    Derived quantities
    ------------------
    subcarrier_spacing_hz : Δf = B / N
    symbol_duration_s     : T  = 1 / Δf  (one time slot)
    frame_duration_s      : T_f = M × T
    delay_resolution_s    : Δτ = 1/B
    range_resolution_m    : Δr = c / (2B)
    doppler_resolution_hz : Δν = Δf / M = B / (N·M)
    total_samples         : N × M  (complex samples per frame)
    """

    N:             int   = 128
    M:             int   = 128
    bandwidth_hz:  float = 10e6

    # ── Derived quantities ────────────────────────────────────────────────────

    @property
    def subcarrier_spacing_hz(self) -> float:
        """Δf = B / N (Hz)."""
        return self.bandwidth_hz / self.N

    @property
    def symbol_duration_s(self) -> float:
        """T = 1 / Δf  (duration of one OTFS time slot, seconds)."""
        return 1.0 / self.subcarrier_spacing_hz

    @property
    def frame_duration_s(self) -> float:
        """T_f = M × T  (total frame duration, seconds)."""
        return self.M * self.symbol_duration_s

    @property
    def delay_resolution_s(self) -> float:
        """Δτ = 1/B  (seconds).  The finest delay the system can resolve."""
        return 1.0 / self.bandwidth_hz

    @property
    def range_resolution_m(self) -> float:
        """Δr = c / (2B)  (metres).  The finest range the system can resolve."""
        return C_MS / (2.0 * self.bandwidth_hz)

    @property
    def doppler_resolution_hz(self) -> float:
        """Δν = Δf / M = B / (N·M)  (Hz)."""
        return self.bandwidth_hz / (self.N * self.M)

    @property
    def total_samples(self) -> int:
        """N × M complex samples per OTFS frame."""
        return self.N * self.M

    @property
    def sampling_rate_hz(self) -> float:
        """Complex sampling rate = B (Hz)."""
        return self.bandwidth_hz

    def delay_bin_to_range_m(self, k: int) -> float:
        """Convert delay bin index k to slant range in metres."""
        return k * self.range_resolution_m

    def range_m_to_delay_bin(self, range_m: float) -> float:
        """Convert slant range (metres) to (fractional) delay bin index."""
        return range_m / self.range_resolution_m

    def doppler_bin_to_hz(self, l: int) -> float:
        """Convert Doppler bin index l to frequency shift in Hz."""
        return l * self.doppler_resolution_hz

    def doppler_hz_to_bin(self, doppler_hz: float) -> float:
        """Convert Doppler frequency shift (Hz) to (fractional) bin index."""
        return doppler_hz / self.doppler_resolution_hz

    def __repr__(self) -> str:
        return (
            f"OTFSGrid(N={self.N}, M={self.M}, "
            f"B={self.bandwidth_hz/1e6:.1f} MHz, "
            f"Δr={self.range_resolution_m:.2f} m, "
            f"Δν={self.doppler_resolution_hz:.2f} Hz, "
            f"T_f={self.frame_duration_s*1e3:.2f} ms)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Transforms
# ═══════════════════════════════════════════════════════════════════════════════

def isfft(X_dd: np.ndarray) -> np.ndarray:
    """
    Inverse Symplectic Finite Fourier Transform (ISFFT).

    Maps information symbols from the delay-Doppler domain to the
    time-frequency domain.

        X_TF[n,m] = Σ_k Σ_l X_DD[k,l] · exp(+j2πnk/N) · exp(-j2πml/M)

    Implementation: FFT along the Doppler axis (axis=1), then IFFT along
    the delay axis (axis=0).  This matches the convention in Raviteja et al.
    (2018a), Eq. (3).

    Parameters
    ----------
    X_dd : np.ndarray, shape (N, M), complex
        Delay-Doppler domain symbols.
        axis 0 (rows) = delay bins.
        axis 1 (cols) = Doppler bins.

    Returns
    -------
    X_tf : np.ndarray, shape (N, M), complex
        Time-frequency domain representation.
        axis 0 (rows) = time slots.
        axis 1 (cols) = sub-carriers.

    Notes
    -----
    Round-trip identity: sfft(isfft(X)) ≈ X  (subject to numerical precision).
    """
    if X_dd.ndim != 2:
        raise ValueError(f"X_dd must be 2D, got shape {X_dd.shape}")
    # Step 1: DFT along Doppler axis (axis=1): maps l → m
    # Step 2: IDFT along delay axis (axis=0): maps k → n
    return np.fft.ifft(np.fft.fft(X_dd, axis=1), axis=0)


def sfft(Y_tf: np.ndarray) -> np.ndarray:
    """
    Symplectic Finite Fourier Transform (SFFT).

    Maps received time-frequency symbols back to the delay-Doppler domain.

        Y_DD[k,l] = (1/NM) Σ_n Σ_m Y_TF[n,m] · exp(-j2πnk/N) · exp(+j2πml/M)

    Implementation: IFFT along the Doppler axis (axis=1), then FFT along
    the delay axis (axis=0) — the exact inverse of isfft.

    Parameters
    ----------
    Y_tf : np.ndarray, shape (N, M), complex
        Time-frequency domain received signal.

    Returns
    -------
    Y_dd : np.ndarray, shape (N, M), complex
        Delay-Doppler domain received symbols.
    """
    if Y_tf.ndim != 2:
        raise ValueError(f"Y_tf must be 2D, got shape {Y_tf.shape}")
    # Step 1: IDFT along Doppler axis (axis=1): maps m → l
    # Step 2: DFT along delay axis (axis=0): maps n → k
    return np.fft.fft(np.fft.ifft(Y_tf, axis=1), axis=0)


# ═══════════════════════════════════════════════════════════════════════════════
# Transmitter
# ═══════════════════════════════════════════════════════════════════════════════

class OTFSTransmitter:
    """
    OTFS transmitter: delay-Doppler symbols → transmitted time-domain frame.

    TX chain:
        X_DD (N×M) → ISFFT → X_TF (N×M) → Heisenberg → s (NM,)

    Rectangular pulse shaping is used throughout (Raviteja et al. 2018b):
    each row of X_TF is transmitted as one OFDM-style symbol burst.
    The Heisenberg transform reduces to a flat concatenation of the
    N rows of X_TF, each of length M.

    Parameters
    ----------
    grid : OTFSGrid
        Grid parameters (N, M, bandwidth).
    """

    def __init__(self, grid: OTFSGrid) -> None:
        self.grid = grid

    def modulate(self, X_dd: np.ndarray) -> np.ndarray:
        """
        Modulate a delay-Doppler symbol array into a transmitted frame.

        Parameters
        ----------
        X_dd : np.ndarray, shape (N, M), complex
            Delay-Doppler symbols.  May contain pilot, guard, and data
            symbols — the transmitter does not distinguish between them.

        Returns
        -------
        s : np.ndarray, shape (N*M,), complex
            Transmitted complex baseband samples at sampling rate B.

        Notes
        -----
        For rectangular pulse shaping:
            s[n*M + m] = X_TF[n, m]   for all n, m

        The total frame duration is N*M / B seconds.
        """
        if X_dd.shape != (self.grid.N, self.grid.M):
            raise ValueError(
                f"X_dd shape {X_dd.shape} does not match "
                f"grid ({self.grid.N}, {self.grid.M})"
            )
        # ISFFT: DD → TF
        X_tf = isfft(X_dd)

        # Heisenberg transform (rectangular pulse): TF → time domain
        # Each row n of X_tf becomes M consecutive time-domain samples
        return self._heisenberg(X_tf)

    def _heisenberg(self, X_tf: np.ndarray) -> np.ndarray:
        """
        Heisenberg transform with rectangular pulse shaping.

        For rectangular (boxcar) transmit pulse g_tx:
            s[n*M : (n+1)*M] = IFFT{ X_TF[n, :] }

        Taking IFFT along each row converts M sub-carrier coefficients
        to M time-domain samples (one OFDM symbol per row of X_TF).
        The result is flattened to a 1D array of N*M samples.

        References
        ----------
        Raviteja et al. (2018b), Section II-A, Eq. (2).
        """
        N, M = self.grid.N, self.grid.M
        # IFFT along sub-carrier axis (axis=1): one OFDM symbol per row
        s_matrix = np.fft.ifft(X_tf, axis=1) * M   # scale by M for power normalisation
        return s_matrix.flatten()


# ═══════════════════════════════════════════════════════════════════════════════
# Receiver
# ═══════════════════════════════════════════════════════════════════════════════

class OTFSReceiver:
    """
    OTFS receiver: received time-domain frame → delay-Doppler symbols.

    RX chain:
        r (NM,) → Wigner-Ville → Y_TF (N×M) → SFFT → Y_DD (N×M)

    Rectangular receive pulse assumed (matched to rectangular transmit pulse).

    Parameters
    ----------
    grid : OTFSGrid
        Grid parameters.  Must match the transmitter's grid.
    """

    def __init__(self, grid: OTFSGrid) -> None:
        self.grid = grid

    def demodulate(self, r: np.ndarray) -> np.ndarray:
        """
        Demodulate a received time-domain frame to the delay-Doppler domain.

        Parameters
        ----------
        r : np.ndarray, shape (N*M,), complex
            Received complex baseband samples.

        Returns
        -------
        Y_dd : np.ndarray, shape (N, M), complex
            Delay-Doppler domain received symbols.

        Notes
        -----
        The Wigner-Ville filter with rectangular pulse shapes reshapes
        the received block to (N, M) and applies FFT along the sub-carrier
        axis to recover the time-frequency representation.  SFFT then
        maps this to the delay-Doppler domain.
        """
        N, M = self.grid.N, self.grid.M
        if r.size != N * M:
            raise ValueError(
                f"Received signal length {r.size} ≠ N*M = {N*M}"
            )
        # Wigner-Ville filter: reshape and FFT along sub-carrier axis
        Y_tf = self._wigner_ville(r)

        # SFFT: TF → DD
        return sfft(Y_tf)

    def _wigner_ville(self, r: np.ndarray) -> np.ndarray:
        """
        Wigner-Ville receive filter with rectangular pulse shaping.

        Reshapes the 1D received frame to (N, M) and applies FFT along
        the sub-carrier axis (axis=1) to recover Y_TF.

        For rectangular receive pulse g_rx:
            Y_TF[n, m] = FFT{ r[n*M : (n+1)*M] }[m]

        References
        ----------
        Raviteja et al. (2018b), Section II-B, Eq. (6).
        """
        N, M = self.grid.N, self.grid.M
        r_matrix = r.reshape(N, M)
        return np.fft.fft(r_matrix, axis=1) / M   # undo the M scale from Heisenberg


# ═══════════════════════════════════════════════════════════════════════════════
# Channel models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DelayDopplerChannel:
    """
    Discrete delay-Doppler channel for simulation.

    Models a channel with one or more scatterers, each characterised
    by an integer delay index, an integer Doppler index, and a complex
    path gain.

    In the delay-Doppler domain, the input-output relationship is
    a 2D circular convolution:

        Y_DD[k,l] = Σ_p h_p · X_DD[(k - k_p) % N, (l - l_p) % M] + noise

    Parameters
    ----------
    taps : list of (k_p, l_p, h_p) tuples
        Each tuple is (delay_bin: int, doppler_bin: int, gain: complex).
        Integer indices only — fractional delay/Doppler requires additional
        treatment (see Raviteja et al. 2018b, Section III).
    noise_power : float
        Additive white Gaussian noise power (complex variance σ² = noise_power).
        Set to 0.0 for noiseless simulation.
    rng : np.random.Generator, optional
        Random number generator for noise.  Uses np.random.default_rng() if None.
    """
    taps:        list          # [(k_p, l_p, h_p), ...]
    noise_power: float = 0.0
    rng:         object = None

    def __post_init__(self) -> None:
        if self.rng is None:
            self.rng = np.random.default_rng(42)

    def apply(self, X_dd: np.ndarray) -> np.ndarray:
        """
        Apply the delay-Doppler channel to an input symbol array.

        Parameters
        ----------
        X_dd : np.ndarray, shape (N, M), complex
            Transmit delay-Doppler symbols.

        Returns
        -------
        Y_dd : np.ndarray, shape (N, M), complex
            Received delay-Doppler symbols (channel output + noise).
        """
        N, M = X_dd.shape
        Y_dd = np.zeros_like(X_dd)

        for k_p, l_p, h_p in self.taps:
            # Implement Y_dd[k,l] = h_p * X_dd[(k-k_p)%N, (l-l_p)%M]
            # np.roll(X, +k_p, axis=0)[k,l] = X[(k-k_p)%N, l]  ← correct
            Y_dd += h_p * np.roll(np.roll(X_dd, k_p, axis=0), l_p, axis=1)

        # Additive white Gaussian noise (complex)
        if self.noise_power > 0.0:
            noise = self.rng.normal(
                0.0, math.sqrt(self.noise_power / 2.0), size=(N, M)
            ) + 1j * self.rng.normal(
                0.0, math.sqrt(self.noise_power / 2.0), size=(N, M)
            )
            Y_dd += noise

        return Y_dd

    def apply_through_time_domain(
        self,
        s: np.ndarray,
        grid: OTFSGrid,
        noise_power: Optional[float] = None,
    ) -> np.ndarray:
        """
        Apply the channel in the time domain (physically accurate path).

        For each tap (k_p, l_p, h_p):
          1. Delay the signal by k_p samples.
          2. Apply a Doppler phase rotation exp(j·2π·l_p·n / (N·M)).
          3. Scale by h_p.
          4. Sum all tap contributions and add AWGN.

        Parameters
        ----------
        s : np.ndarray, shape (N*M,), complex
            Transmitted complex baseband samples.
        grid : OTFSGrid
            Grid parameters for computing phase rotation rates.
        noise_power : float, optional
            Override the instance noise_power for this call.

        Returns
        -------
        r : np.ndarray, shape (N*M,), complex
            Received complex baseband samples.
        """
        NM = grid.N * grid.M
        if s.size != NM:
            raise ValueError(f"Signal length {s.size} ≠ N*M = {NM}")

        r = np.zeros(NM, dtype=complex)
        n_vec = np.arange(NM, dtype=float)

        for k_p, l_p, h_p in self.taps:
            # Delay: circular shift by k_p samples
            s_delayed = np.roll(s, k_p)
            # Doppler: phase rotation exp(j·2π·l_p·n / (N·M))
            phase_vec = np.exp(1j * 2.0 * math.pi * l_p * n_vec / NM)
            r += h_p * phase_vec * s_delayed

        # AWGN
        _noise_power = noise_power if noise_power is not None else self.noise_power
        if _noise_power > 0.0:
            r += (
                self.rng.normal(0.0, math.sqrt(_noise_power / 2.0), NM)
                + 1j * self.rng.normal(0.0, math.sqrt(_noise_power / 2.0), NM)
            )

        return r


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: SNR and noise power helpers
# ═══════════════════════════════════════════════════════════════════════════════

def snr_db_to_noise_power(snr_db: float, signal_power: float = 1.0) -> float:
    """
    Convert SNR in dB to noise variance σ² given signal power.

    σ² = signal_power / (10^(SNR_dB/10))
    """
    return signal_power / (10.0 ** (snr_db / 10.0))


def noise_power_to_snr_db(noise_power: float, signal_power: float = 1.0) -> float:
    """Convert noise variance σ² to SNR in dB."""
    return 10.0 * math.log10(signal_power / max(noise_power, 1e-30))


# ═══════════════════════════════════════════════════════════════════════════════
# Utility: pilot echo localisation (navigation function)
# ═══════════════════════════════════════════════════════════════════════════════

def find_pilot_echo(
    Y_dd:           np.ndarray,
    pilot_k:        int,
    pilot_l:        int,
    search_radius_k: int = 5,
    search_radius_l: int = 10,
) -> tuple[int, int, float]:
    """
    Locate the pilot echo peak in the received delay-Doppler grid.

    Searches a region around (pilot_k, pilot_l) for the peak amplitude.
    Used by the navigation receiver to extract the pseudorange from the
    pilot echo delay coordinate.

    Parameters
    ----------
    Y_dd : np.ndarray, shape (N, M), complex
        Received delay-Doppler grid.
    pilot_k : int
        Transmitted pilot delay bin (known to the receiver).
    pilot_l : int
        Transmitted pilot Doppler bin (known to the receiver).
    search_radius_k : int
        Search radius in the delay axis. Default 5 bins.
    search_radius_l : int
        Search radius in the Doppler axis. Default 10 bins.

    Returns
    -------
    (peak_k, peak_l, peak_amplitude) : tuple
        peak_k : int — delay bin of the detected echo
        peak_l : int — Doppler bin of the detected echo
        peak_amplitude : float — magnitude of the peak
    """
    N, M = Y_dd.shape
    best_k, best_l, best_amp = pilot_k, pilot_l, 0.0

    for dk in range(-search_radius_k, search_radius_k + 1):
        for dl in range(-search_radius_l, search_radius_l + 1):
            k = (pilot_k + dk) % N
            l = (pilot_l + dl) % M
            amp = abs(Y_dd[k, l])
            if amp > best_amp:
                best_amp = amp
                best_k   = k
                best_l   = l

    return best_k, best_l, best_amp


def extract_pseudorange(
    peak_k:    int,
    grid:      OTFSGrid,
) -> float:
    """
    Extract pseudorange estimate from a detected pilot echo delay bin.

    The pilot echo at delay bin k_echo corresponds to a round-trip
    propagation delay of:
        τ = k_echo × Δτ = k_echo / B

    The one-way pseudorange is:
        ρ̂ = c × τ / 2 = c × k_echo / (2B) = k_echo × Δr

    Parameters
    ----------
    peak_k : int
        Detected delay bin of the pilot echo.
    grid : OTFSGrid
        Grid parameters (provides Δr = c/2B).

    Returns
    -------
    pseudorange_m : float
        Estimated one-way range in metres.
    """
    return peak_k * grid.range_resolution_m


def extract_doppler_velocity(
    peak_l:          int,
    grid:            OTFSGrid,
    carrier_freq_hz: float,
) -> float:
    """
    Extract radial velocity estimate from a detected pilot echo Doppler bin.

    The pilot echo at Doppler bin l_echo corresponds to a frequency shift:
        ν = l_echo × Δν

    The radial velocity is:
        ṙ = ν × c / f_carrier = l_echo × Δν × c / f_carrier

    Parameters
    ----------
    peak_l : int
        Detected Doppler bin of the pilot echo.
    grid : OTFSGrid
        Grid parameters (provides Δν).
    carrier_freq_hz : float
        Carrier frequency in Hz.

    Returns
    -------
    range_rate_ms : float
        Estimated radial range rate in m/s.
        Positive = satellite receding (range increasing).
    """
    doppler_hz = peak_l * grid.doppler_resolution_hz
    return doppler_hz * C_MS / carrier_freq_hz
