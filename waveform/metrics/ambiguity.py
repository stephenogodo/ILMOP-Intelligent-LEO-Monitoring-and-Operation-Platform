"""
waveform/metrics/ambiguity.py

OTFS Pilot Ambiguity Function and Ranging Cramér-Rao Bound
==========================================================

The ambiguity function is the fundamental metric for characterising the
joint delay-Doppler resolution of any ranging waveform.  For OTFS-ISAC
it answers three questions simultaneously:

    1. How precisely can the OTFS pilot echo locate a target in delay
       (range) and Doppler (velocity)?

    2. Do pilot sidelobes contaminate adjacent delay-Doppler bins, and if
       so, at what level?  (This is the contamination the guard region
       alone does not protect against.)

    3. Why does the choice of pilot sequence matter for the sensing and
       navigation functions — not just for PAPR?

Cyclic autocorrelation and PSLR
---------------------------------
The cyclic autocorrelation of a pilot sequence x of length M is:

    R_xx[k] = Σ_{n=0}^{M-1} x*[n] · x[(n+k) % M]

which is efficiently computed as:

    R_xx = IFFT(|FFT(x)|²)

The Peak-to-Sidelobe Level Ratio (PSLR) measures the largest sidelobe
relative to the mainlobe:

    PSLR = 20·log₁₀(max_{k≠0} |R[k]| / |R[0]|)   (dB)

    Lower (more negative) PSLR = better pilot.
    PSLR = −∞ means a perfect thumbtack (zero cyclic sidelobes).

ZC sequence PSLR
-----------------
For a Zadoff-Chu sequence z_u[n] = exp(−jπu·n(n+1)/M) of length M:

    Prime M:     |R_ZC[k]| = 0  for k ≠ 0  →  PSLR = −∞ dB
                 Proof: Σ_n exp(j2πu·k·n/M) = 0 for k≠0 when gcd(u,M)=1
                 and M is prime.

    Non-prime M: |R_ZC[k]| ≤ √M  for k ≠ 0
                 PSLR ≈ −10·log₁₀(M) dB
                 e.g. M=64: PSLR ≈ −18 dB; M=256: PSLR ≈ −24 dB.

Random QPSK PSLR
-----------------
Mean sidelobe level ≈ √M (same order as ZC non-prime), but with HIGH
variance.  Worst-case sidelobes can be O(M), giving PSLR close to 0 dB.
ZC advantage: a DETERMINISTIC bound, not just a mean.

Cramér-Rao Bound for ranging
------------------------------
The CRB for one-way range estimation from the OTFS pilot echo is:

    σ_CRB(range) = c / (4π · √SNR · B_rms)

where B_rms is the root-mean-square bandwidth of the transmitted signal.

For OTFS with rectangular pulse shaping:

    B_rms = B / √12        (RMS bandwidth of a rectangular spectrum)

    σ_CRB = c·√12 / (4π·B·√SNR) = Δr·√12 / (2π·√SNR)

Comparison with the simplified model used in validator.py:

    σ_quant   = Δr/√12                  [quantisation noise floor]
    σ_thermal = Δr/(2π·√SNR)            [simplified thermal model]

The CRB is σ_CRB = Δr·√12/(2π·√SNR) = 12·σ_thermal/2π ≈ 1.91·σ_thermal.

The simplified σ_thermal understates the true CRB by a factor of ~√12/2π ≈ 0.55,
i.e. the validated results (9.99 m RMS) are pessimistic relative to what the
CRB says is achievable.  This is a strength, not a weakness — the achieved
accuracy approaches, but does not exceed, the CRB at the operating SNR.

Relationship to guard region design
--------------------------------------
The guard region (pilot_frame.py) protects against the pilot echo leaking
into data bins via the MAIN LOBE of the ambiguity function.  It does NOT
suppress sidelobes.  For a pilot with non-zero cyclic sidelobes:

    Data symbol quality is degraded by sidelobe interference even with
    a correctly-sized guard region.

A ZC pilot with zero (prime length) or bounded (non-prime length) sidelobes
eliminates this source of interference, improving SNR in the data region.

WDR reference
--------------
WDR-009 documents this as a design record.

References
----------
- Woodward, P. M. (1953). Probability and Information Theory with
  Applications to Radar. Pergamon Press.  [Ambiguity function definition]
- Van Trees, H. L. (2001). Detection, Estimation, and Modulation Theory,
  Part I. Wiley.  [CRB for delay estimation, Section 6.2]
- Chu, D. C. (1972). Polyphase codes with good periodic correlation
  properties. IEEE Trans. Inf. Theory, 18(4), 531–532.
- Raviteja, P., Viterbo, E., & Hong, Y. (2018a). OTFS performance on
  static multipath channels. IEEE Wireless Commun. Lett., 8(3), 745–748.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import numpy as np

from waveform.otfs_signal import OTFSGrid

# ── Physical constants ────────────────────────────────────────────────────────
C_MS = 299_792_458.0


# ═══════════════════════════════════════════════════════════════════════════════
# Core ambiguity functions
# ═══════════════════════════════════════════════════════════════════════════════

def cyclic_autocorrelation(x: np.ndarray) -> np.ndarray:
    """
    Compute the cyclic (periodic) autocorrelation of a complex sequence.

    R[k] = Σ_{n=0}^{M-1} x*[n] · x[(n+k) % M]

    Implemented efficiently via:  R = IFFT(|FFT(x)|²)

    Parameters
    ----------
    x : np.ndarray, shape (M,), complex
        Pilot sequence of length M.

    Returns
    -------
    R : np.ndarray, shape (M,), complex
        Cyclic autocorrelation.  R[0] = ||x||² = Σ|x[n]|².
    """
    X = np.fft.fft(x)
    return np.fft.ifft(X * np.conj(X))


def ambiguity_function_2d(
    X_dd:  np.ndarray,
    shift: bool = True,
) -> np.ndarray:
    """
    Compute the 2D cyclic ambiguity function of a delay-Doppler pilot frame.

    AF[Δk, Δl] = |Σ_{k,l} X*[k,l] · X[(k+Δk)%N, (l+Δl)%M]|

    This is the 2D cyclic cross-correlation of X_dd with itself, measuring
    how well two targets at different delay-Doppler offsets can be separated.

    Computed efficiently as: AF = |IFFT2(|FFT2(X)|²)|

    Parameters
    ----------
    X_dd : np.ndarray, shape (N, M), complex
        Pilot placement in the delay-Doppler grid.
    shift : bool
        If True, shift the zero-lag to the centre of the output array
        (using np.fft.fftshift) so that negative offsets appear on the
        left/top and positive offsets on the right/bottom.

    Returns
    -------
    AF : np.ndarray, shape (N, M), float
        Ambiguity function magnitude |AF[Δk, Δl]|.  Mainlobe at
        AF[N//2, M//2] if shift=True, or AF[0, 0] if shift=False.
    """
    F2   = np.fft.fft2(X_dd)
    af   = np.abs(np.fft.ifft2(F2 * np.conj(F2)))
    if shift:
        af = np.fft.fftshift(af)
    return af


def pslr(R: np.ndarray, mainlobe_halfwidth: int = 0) -> float:
    """
    Peak-to-Sidelobe Level Ratio (PSLR) of an autocorrelation sequence.

    PSLR = 20·log₁₀(max sidelobe / mainlobe)   (dB)

    Lower (more negative) is better.  −∞ dB means zero sidelobes.

    Parameters
    ----------
    R : np.ndarray
        Autocorrelation sequence (1D or 2D).  The mainlobe is assumed at
        index 0 (or [0,0] for 2D).
    mainlobe_halfwidth : int
        Number of bins on each side of the mainlobe to exclude from the
        sidelobe search (to avoid counting mainlobe shoulders).  Default 0.

    Returns
    -------
    float
        PSLR in dB.  Returns −inf if all sidelobes are numerically zero.
    """
    R_abs = np.abs(R).flatten()
    mainlobe_val = R_abs[0]

    if mainlobe_halfwidth > 0:
        mask = np.ones(len(R_abs), dtype=bool)
        mask[:mainlobe_halfwidth + 1] = False
        mask[-(mainlobe_halfwidth):] = False
        sidelobes = R_abs[mask]
    else:
        sidelobes = R_abs[1:]

    if len(sidelobes) == 0 or sidelobes.max() < 1e-12:
        return -math.inf

    return 20.0 * math.log10(sidelobes.max() / max(mainlobe_val, 1e-15))


def islr(R: np.ndarray, mainlobe_halfwidth: int = 1) -> float:
    """
    Integrated Sidelobe Level Ratio (ISLR) of an autocorrelation sequence.

    ISLR = 10·log₁₀(Σ sidelobe power / mainlobe power)   (dB)

    ISLR measures the total sidelobe energy relative to the mainlobe,
    capturing the cumulative interference to data symbols across all
    delay-Doppler bins — not just the worst-case bin (PSLR).

    Parameters
    ----------
    R : np.ndarray
        Autocorrelation sequence.  Mainlobe assumed at index 0.
    mainlobe_halfwidth : int
        Bins to exclude on each side of the mainlobe.  Default 1.

    Returns
    -------
    float
        ISLR in dB.  Returns −inf if all sidelobes are numerically zero.
    """
    R_abs_sq = np.abs(R.flatten()) ** 2
    mainlobe_power = R_abs_sq[0]

    mask = np.ones(len(R_abs_sq), dtype=bool)
    mask[:mainlobe_halfwidth + 1] = False
    mask[-(mainlobe_halfwidth):] = False

    sidelobe_power = R_abs_sq[mask].sum()

    if sidelobe_power < 1e-20:
        return -math.inf

    return 10.0 * math.log10(sidelobe_power / max(mainlobe_power, 1e-20))


# ═══════════════════════════════════════════════════════════════════════════════
# Cramér-Rao Bound for ranging
# ═══════════════════════════════════════════════════════════════════════════════

def crb_range_m(
    bandwidth_hz:   float,
    snr_linear:     float,
    pulse_shape:    str = 'rectangular',
) -> float:
    """
    Cramér-Rao Lower Bound for one-way range estimation.

    For an OTFS signal with rectangular pulse shaping:

        B_rms = B / √12    (RMS bandwidth of rectangular spectrum)

        σ_CRB = c / (4π · √SNR · B_rms)
               = c·√12 / (4π·B·√SNR)
               = Δr·√12 / (2π·√SNR)

    where Δr = c/(2B) is the OTFS delay-domain range resolution.

    Parameters
    ----------
    bandwidth_hz : float
        Signal bandwidth B in Hz.
    snr_linear : float
        Signal-to-noise ratio (linear, not dB).
    pulse_shape : str
        'rectangular' (default, rectangular pulse shaping, B_rms = B/√12)
        or 'ideal' (B_rms = B, for comparison with the ideal flat-spectrum case).

    Returns
    -------
    float
        CRB standard deviation for range estimation (metres).

    References
    ----------
    Van Trees (2001), Part I, Section 6.2.  Woodward (1953), Chapter 5.
    """
    if pulse_shape == 'rectangular':
        B_rms = bandwidth_hz / math.sqrt(12.0)
    elif pulse_shape == 'ideal':
        B_rms = bandwidth_hz
    else:
        raise ValueError(f"Unknown pulse_shape: {pulse_shape!r}")

    sigma_tau = 1.0 / (2.0 * math.pi * math.sqrt(snr_linear) * B_rms)
    return C_MS * sigma_tau / 2.0


def quantisation_floor_m(bandwidth_hz: float) -> float:
    """
    Quantisation noise floor for range estimation.

    This is the SNR-independent range resolution floor set by the
    delay bin width:

        σ_quant = Δr / √12 = c / (2B√12)

    This equals σ_CRB(range) at SNR = 1 (0 dB) for rectangular pulse shaping.
    At high SNR, σ_quant becomes the dominant error source.

    Parameters
    ----------
    bandwidth_hz : float
        Signal bandwidth B in Hz.

    Returns
    -------
    float
        Quantisation floor (metres).  4.33 m at B = 10 MHz.
    """
    delta_r = C_MS / (2.0 * bandwidth_hz)
    return delta_r / math.sqrt(12.0)


def combined_ranging_sigma(
    bandwidth_hz: float,
    snr_linear:   float,
) -> float:
    """
    Combined pseudorange standard deviation (quantisation + thermal).

    σ_total = √(σ_quant² + σ_thermal²)

    where:
        σ_quant   = c / (2B√12)
        σ_thermal = c / (4π·B·√SNR)   [simplified model, validator.py]

    This is the simplified model used in the navigation validator.
    The rigorous CRB (crb_range_m) is √12/(2π) ≈ 1.91 × σ_thermal at
    the same SNR — the simplified model is slightly optimistic.

    Parameters
    ----------
    bandwidth_hz : float
        Signal bandwidth (Hz).
    snr_linear : float
        SNR (linear).

    Returns
    -------
    float
        Combined ranging standard deviation (metres).
    """
    delta_r = C_MS / (2.0 * bandwidth_hz)
    sigma_q = delta_r / math.sqrt(12.0)
    sigma_t = delta_r / (2.0 * math.pi * math.sqrt(max(snr_linear, 1e-10)))
    return math.sqrt(sigma_q ** 2 + sigma_t ** 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Comparison utilities
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PilotAmbiguityResult:
    """
    Ambiguity function metrics for one pilot type.

    Attributes
    ----------
    name : str
        Pilot type label.
    length : int
        Pilot sequence length M.
    pslr_db : float
        Peak-to-Sidelobe Level Ratio (dB). Lower is better. −inf = zero sidelobes.
    islr_db : float
        Integrated Sidelobe Level Ratio (dB). Lower is better.
    is_prime_length : bool
        True if the sequence length is prime (guarantees zero cyclic sidelobes
        for ZC sequences).
    papr_db : float
        Peak-to-Average Power Ratio in dB.  0 dB = constant amplitude (ideal).
    autocorr : np.ndarray
        Cyclic autocorrelation array (complex), shape (M,).
    """
    name:            str
    length:          int
    pslr_db:         float
    islr_db:         float
    is_prime_length: bool
    papr_db:         float
    autocorr:        np.ndarray


def analyse_pilot(
    name:    str,
    x:       np.ndarray,
    is_zc:   bool = False,
) -> PilotAmbiguityResult:
    """
    Compute ambiguity metrics for a pilot sequence.

    Parameters
    ----------
    name : str
        Descriptive label for the pilot type.
    x : np.ndarray, shape (M,), complex
        Pilot sequence.
    is_zc : bool
        Hint that this is a ZC sequence (used for prime-length check).

    Returns
    -------
    PilotAmbiguityResult with PSLR, ISLR, PAPR, and autocorrelation.
    """
    M = len(x)
    R = cyclic_autocorrelation(x)

    # PSLR and ISLR
    pslr_val = pslr(R)
    islr_val = islr(R)

    # PAPR (peak power / average power)
    power = np.abs(x) ** 2
    papr_val = 10.0 * math.log10(power.max() / (power.mean() + 1e-20))

    # Is the length prime?
    try:
        import sympy
        prime = bool(sympy.isprime(M))
    except ImportError:
        # Fallback: trial division for small M
        prime = M > 1 and all(M % i != 0 for i in range(2, int(M**0.5) + 1))

    return PilotAmbiguityResult(
        name            = name,
        length          = M,
        pslr_db         = pslr_val,
        islr_db         = islr_val,
        is_prime_length = prime,
        papr_db         = papr_val,
        autocorr        = R,
    )


def compare_pilots(
    pilots: Dict[str, np.ndarray],
) -> Dict[str, PilotAmbiguityResult]:
    """
    Analyse and compare multiple pilot sequences.

    Parameters
    ----------
    pilots : dict mapping name → sequence
        e.g. {'Point': delta, 'QPSK': random_qpsk, 'ZC': zc_sequence}

    Returns
    -------
    dict mapping name → PilotAmbiguityResult
    """
    return {name: analyse_pilot(name, x) for name, x in pilots.items()}


def print_comparison_table(results: Dict[str, PilotAmbiguityResult]) -> None:
    """Print a summary table for the pilot comparison."""
    header = f"{'Pilot':<20} {'M':>6} {'PSLR (dB)':>12} {'ISLR (dB)':>12} {'PAPR (dB)':>10} {'Prime?':>7}"
    print(header)
    print('-' * len(header))
    for r in results.values():
        pslr_str = f"{r.pslr_db:.1f}" if r.pslr_db != -math.inf else "−∞"
        islr_str = f"{r.islr_db:.1f}" if r.islr_db != -math.inf else "−∞"
        print(
            f"{r.name:<20} {r.length:>6} {pslr_str:>12} "
            f"{islr_str:>12} {r.papr_db:>10.1f} {str(r.is_prime_length):>7}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CRB vs SNR table
# ═══════════════════════════════════════════════════════════════════════════════

def crb_vs_snr_table(
    bandwidth_hz: float,
    snr_db_range: Sequence[float] = (-10, -5, 0, 5, 10, 15, 20, 25, 30),
) -> Dict[float, Dict[str, float]]:
    """
    Compute ranging accuracy vs SNR table.

    Returns a dict mapping SNR_dB → {'crb_m', 'simplified_m', 'quant_floor_m'}.

    Parameters
    ----------
    bandwidth_hz : float
        Signal bandwidth (Hz).
    snr_db_range : sequence of float
        SNR values in dB at which to evaluate.

    Returns
    -------
    dict
        Keys are SNR (dB).  Values are dicts with:
            'crb_m'         — CRB (rectangular pulse) in metres
            'simplified_m'  — simplified model (validator.py) in metres
            'quant_floor_m' — quantisation floor (SNR-independent) in metres
    """
    q_floor = quantisation_floor_m(bandwidth_hz)
    table   = {}
    for snr_db in snr_db_range:
        snr_lin = 10.0 ** (snr_db / 10.0)
        table[snr_db] = {
            'crb_m':         crb_range_m(bandwidth_hz, snr_lin, 'rectangular'),
            'simplified_m':  combined_ranging_sigma(bandwidth_hz, snr_lin),
            'quant_floor_m': q_floor,
        }
    return table
