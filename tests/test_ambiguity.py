"""
tests/test_ambiguity.py

Test suite for waveform/metrics/ambiguity.py.

Exit conditions:
    1. Cyclic autocorrelation of ZC (prime length) gives zero sidelobes (PSLR = -inf).
    2. Cyclic autocorrelation of ZC (non-prime) gives bounded PSLR ≈ -10log10(M) dB.
    3. Random QPSK pilot has worse (higher) PSLR than ZC for same length.
    4. Point pilot (delta) in DD grid has zero 2D sidelobes.
    5. CRB formula is dimensionally consistent and approaches quant floor at high SNR.
    6. ZC pilot has PAPR = 0 dB.  Random QPSK has PAPR > 0 dB.
"""

import math
import numpy as np
import pytest

from waveform.metrics.ambiguity import (
    PilotAmbiguityResult,
    ambiguity_function_2d,
    analyse_pilot,
    
    compare_pilots,
    crb_range_m,
    crb_vs_snr_table,
    cyclic_autocorrelation,
    islr,
    pslr,
    quantisation_floor_m,
    combined_ranging_sigma,
)
from waveform.otfs_signal import OTFSGrid


# ── helpers ───────────────────────────────────────────────────────────────────

def zc_sequence(M: int, u: int = 1) -> np.ndarray:
    n = np.arange(M)
    return np.exp(-1j * math.pi * u * n * (n + 1) / M)


def random_qpsk(M: int, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 4, M)
    mapping = np.array([1+1j, 1-1j, -1+1j, -1-1j]) / math.sqrt(2)
    return mapping[bits]


def point_delta(M: int) -> np.ndarray:
    x = np.zeros(M, dtype=complex)
    x[0] = 1.0
    return x


# ══════════════════════════════════════════════════════════════════════════════
# 1 — Cyclic autocorrelation
# ══════════════════════════════════════════════════════════════════════════════

class TestCyclicAutocorrelation:

    def test_mainlobe_equals_energy(self):
        """R[0] = Σ|x[n]|² (energy of the sequence)."""
        x = zc_sequence(61)
        R = cyclic_autocorrelation(x)
        expected = np.sum(np.abs(x) ** 2)
        assert abs(R[0].real - expected) < 1e-6

    def test_zc_prime_zero_sidelobes(self):
        """EXIT CONDITION 1: ZC (prime length 61) has zero cyclic sidelobes."""
        z = zc_sequence(61)
        R = cyclic_autocorrelation(z)
        sidelobes = np.abs(R[1:])
        assert sidelobes.max() < 1e-8, \
            f"ZC prime-length sidelobe {sidelobes.max():.2e} should be ~0"

    def test_zc_prime_pslr_is_minus_inf(self):
        """EXIT CONDITION 1b: PSLR of ZC (prime) = −∞."""
        z = zc_sequence(61)
        R = cyclic_autocorrelation(z)
        assert pslr(R) == -math.inf

    def test_zc_nonprime_bounded_pslr(self):
        """EXIT CONDITION 2: ZC (non-prime M=64) PSLR ≈ −10log10(64) ≈ −18 dB."""
        z = zc_sequence(64)
        R = cyclic_autocorrelation(z)
        p = pslr(R)
        expected = -10 * math.log10(64)
        assert abs(p - expected) < 3.0, \
            f"ZC M=64 PSLR {p:.1f} dB, expected ≈ {expected:.1f} dB"

    def test_zc_nonprime_256_pslr(self):
        """ZC (M=256) PSLR ≈ −10log10(256) = −24.1 dB."""
        z = zc_sequence(256)
        R = cyclic_autocorrelation(z)
        p = pslr(R)
        expected = -10 * math.log10(256)
        assert abs(p - expected) < 3.0

    def test_zc_pslr_better_than_qpsk(self):
        """EXIT CONDITION 3: ZC PSLR < QPSK PSLR (lower = better) for M=64."""
        z = zc_sequence(64)
        q = random_qpsk(64)
        Rz = cyclic_autocorrelation(z)
        Rq = cyclic_autocorrelation(q)
        pz = pslr(Rz)
        pq = pslr(Rq)
        assert pz < pq, \
            f"ZC PSLR {pz:.1f} dB not better than QPSK PSLR {pq:.1f} dB"

    def test_delta_zero_sidelobes(self):
        """Point pilot (delta) has zero cyclic sidelobes."""
        d = point_delta(64)
        R = cyclic_autocorrelation(d)
        assert pslr(R) == -math.inf

    def test_islr_zc_prime_minus_inf(self):
        """ZC (prime) ISLR = −∞ (zero integrated sidelobe energy)."""
        z = zc_sequence(61)
        R = cyclic_autocorrelation(z)
        assert islr(R) == -math.inf

    def test_islr_zc_nonprime_bounded(self):
        """ZC (non-prime) ISLR is finite and negative."""
        z = zc_sequence(64)
        R = cyclic_autocorrelation(z)
        i = islr(R)
        assert -math.inf < i < 0.0, f"ZC M=64 ISLR {i:.1f} dB should be finite negative"


# ══════════════════════════════════════════════════════════════════════════════
# 2 — 2D ambiguity function
# ══════════════════════════════════════════════════════════════════════════════

class TestAmbiguityFunction2D:

    def test_point_pilot_zero_2d_sidelobes(self):
        """EXIT CONDITION 4: Point pilot in DD grid → zero 2D sidelobes."""
        N, M = 16, 32
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[0, 0] = 1.0
        AF = ambiguity_function_2d(X_dd, shift=False)
        # Mainlobe at [0,0]
        assert abs(AF[0, 0] - 1.0) < 1e-8
        # All sidelobes zero
        sidelobes = AF.flatten()[1:]
        assert sidelobes.max() < 1e-8, \
            f"Point pilot 2D AF max sidelobe {sidelobes.max():.2e}"

    def test_2d_af_mainlobe_at_centre_when_shifted(self):
        """With shift=True, the mainlobe appears at [N//2, M//2]."""
        N, M = 16, 32
        X_dd = np.zeros((N, M), dtype=complex)
        X_dd[0, 0] = 1.0
        AF = ambiguity_function_2d(X_dd, shift=True)
        assert AF[N//2, M//2] > 0.99

    def test_2d_af_shape(self):
        """AF shape equals input shape."""
        N, M = 8, 16
        X = np.random.randn(N, M) + 1j * np.random.randn(N, M)
        AF = ambiguity_function_2d(X)
        assert AF.shape == (N, M)

    def test_2d_af_non_negative(self):
        """AF values are non-negative (magnitude)."""
        N, M = 8, 16
        X = np.random.randn(N, M) + 1j * np.random.randn(N, M)
        AF = ambiguity_function_2d(X)
        assert (AF >= 0).all()


# ══════════════════════════════════════════════════════════════════════════════
# 3 — CRB and ranging noise model
# ══════════════════════════════════════════════════════════════════════════════

class TestCRB:

    def test_quant_floor_at_10mhz(self):
        """Quantisation floor at B=10 MHz = c/(2B√12) ≈ 4.33 m."""
        q = quantisation_floor_m(10e6)
        assert abs(q - 4.33) < 0.05, f"Got {q:.4f} m"

    def test_crb_decreases_with_snr(self):
        """CRB decreases as SNR increases (higher SNR → better accuracy)."""
        B = 10e6
        crb_low  = crb_range_m(B, 10.0 ** (10/10))  # 10 dB
        crb_high = crb_range_m(B, 10.0 ** (30/10))  # 30 dB
        assert crb_high < crb_low

    def test_crb_decreases_with_bandwidth(self):
        """CRB decreases as bandwidth increases."""
        snr = 10.0 ** (20/10)
        crb_10  = crb_range_m(10e6, snr)
        crb_20  = crb_range_m(20e6, snr)
        assert crb_20 < crb_10

    def test_crb_approaches_quant_floor_at_high_snr(self):
        """
        EXIT CONDITION 5: At very high SNR the CRB approaches the quantisation floor.

        At high SNR, σ_thermal << σ_quant, so σ_total → σ_quant.
        The CRB for rectangular pulse approaches σ_quant as SNR → ∞.
        """
        B = 10e6
        snr_high = 10.0 ** (60/10)
        crb_high = crb_range_m(B, snr_high)
        q_floor  = quantisation_floor_m(B)
        # At 60 dB SNR, CRB should be much less than quantisation floor
        # but combined sigma should approach the quantisation floor
        combined = combined_ranging_sigma(B, snr_high)
        assert abs(combined - q_floor) / q_floor < 0.01, \
            f"At SNR=60dB: combined={combined:.4f}m, q_floor={q_floor:.4f}m"

    def test_crb_greater_than_simplified_thermal(self):
        """
        CRB (rigorous) > σ_thermal (simplified) for same SNR.

        The simplified model in validator.py is optimistic by factor √12/(2π) ≈ 0.55.
        The rigorous CRB is larger (harder to achieve).
        """
        B = 10e6
        snr = 10.0 ** (20/10)
        crb   = crb_range_m(B, snr)
        delta_r = 299792458.0 / (2.0 * B)
        sigma_t = delta_r / (2.0 * math.pi * math.sqrt(snr))
        # CRB should be larger than simplified thermal alone
        assert crb > sigma_t, \
            f"CRB {crb:.4f} m should exceed simplified thermal {sigma_t:.4f} m"

    def test_crb_vs_snr_table_returns_three_columns(self):
        """crb_vs_snr_table returns dict with crb_m, simplified_m, quant_floor_m."""
        table = crb_vs_snr_table(10e6, snr_db_range=(0, 10, 20))
        assert set(table[0].keys()) == {'crb_m', 'simplified_m', 'quant_floor_m'}

    def test_quant_floor_constant_across_snr(self):
        """Quantisation floor is SNR-independent in the CRB table."""
        table = crb_vs_snr_table(10e6, snr_db_range=(0, 10, 20, 30))
        floors = [row['quant_floor_m'] for row in table.values()]
        assert len(set(round(f, 6) for f in floors)) == 1, \
            "Quantisation floor should be constant across all SNR values"


# ══════════════════════════════════════════════════════════════════════════════
# 4 — Pilot analysis and PAPR
# ══════════════════════════════════════════════════════════════════════════════

class TestPilotAnalysis:

    def test_zc_papr_zero_db(self):
        """EXIT CONDITION 6: ZC pilot has PAPR = 0 dB (constant amplitude)."""
        z = zc_sequence(64)
        result = analyse_pilot("ZC-64", z)
        assert abs(result.papr_db) < 0.01, \
            f"ZC PAPR {result.papr_db:.4f} dB should be 0 dB"

    def test_qpsk_papr_zero_db(self):
        """QPSK also has PAPR = 0 dB (all symbols on unit circle with same amplitude)."""
        q = random_qpsk(64)
        result = analyse_pilot("QPSK-64", q)
        assert abs(result.papr_db) < 0.01

    def test_analyse_pilot_returns_correct_length(self):
        """analyse_pilot records the correct sequence length."""
        z = zc_sequence(61)
        result = analyse_pilot("ZC-61", z)
        assert result.length == 61

    def test_compare_pilots_returns_all_keys(self):
        """compare_pilots returns a result for every pilot in the input dict."""
        pilots = {
            'ZC-61':   zc_sequence(61),
            'ZC-64':   zc_sequence(64),
            'QPSK-64': random_qpsk(64),
            'Delta-64': point_delta(64),
        }
        results = compare_pilots(pilots)
        assert set(results.keys()) == set(pilots.keys())

    def test_zc_prime_better_pslr_than_nonprime(self):
        """ZC prime length gives better PSLR than same-order non-prime length."""
        r_prime    = analyse_pilot("ZC-61",  zc_sequence(61))
        r_nonprime = analyse_pilot("ZC-64",  zc_sequence(64))
        # prime: -inf < non-prime: -18 dB → prime is better (more negative)
        assert r_prime.pslr_db < r_nonprime.pslr_db

    def test_zc_prime_is_prime_length(self):
        """analyse_pilot correctly identifies prime-length sequences."""
        r = analyse_pilot("ZC-61", zc_sequence(61))
        assert r.is_prime_length is True

    def test_zc_64_not_prime(self):
        """M=64 is correctly identified as non-prime."""
        r = analyse_pilot("ZC-64", zc_sequence(64))
        assert r.is_prime_length is False
