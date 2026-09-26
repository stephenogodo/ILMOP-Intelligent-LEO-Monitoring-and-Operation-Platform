# WDR-005 — Generalised OTFS grid sizing constraint for LEO ISAC

**Date:** September 2026
**Status:** Accepted — updated with full generalisation across frequency bands
**Author:** Stephen O. Ogodo

---

## Context

The pilot frame guard region in the Doppler direction must accommodate the maximum
channel Doppler shift ν_max. The guard half-width in bins is:

    l_guard = ceil(ν_max / Δν)

where Δν = B / (N × M) is the Doppler resolution. The guard must fit within M:

    2 × l_guard + 1 ≤ M

This places a constraint on the OTFS grid dimensions that depends on the frequency
band, signal bandwidth, and orbital altitude.

---

## Derivation — generalised form

The maximum Doppler shift for a LEO satellite in circular orbit at altitude h:

    ν_max = v_orb × f_c / c

where:
    v_orb = sqrt(μ / (R_E + h))   [orbital velocity, m/s]
    f_c   = carrier frequency     [Hz]
    c     = 299,792,458 m/s
    μ     = 3.986004418 × 10¹⁴ m³/s²
    R_E   = 6,371,000 m

Substituting into 2 × l_guard + 1 ≤ M, and using l_guard = ν_max × N × M / B:

    2 × (ν_max × N × M / B) + 1 ≤ M
    →  N ≤ B / (2 × ν_max)
    →  N ≤ (B × c) / (2 × v_orb × f_c)

**General closed-form constraint:**

    N_max = floor( B × c / (2 × v_orb × f_c) )
          = floor( B / (2 × ν_max) )

This constraint:
- Scales linearly with bandwidth B — wider bandwidth allows more delay bins
- Scales inversely with carrier frequency f_c — higher bands are severely constrained
- Is nearly independent of altitude — v_orb varies by only ~6% from h=350 km to h=1200 km

---

## Core insight — why increasing M alone never works

Δν = B/(N×M), so l_guard = ν_max/Δν = ν_max×N×M/B scales with N×M.
When M increases, Δν gets finer but l_guard grows proportionally.
The guard-to-frame ratio stays approximately constant regardless of M.

**The constraint is on N, not M.**

---

## Verification — the natural first attempts both fail (S-band, B=10 MHz)

    N=M=128:    Δν=610 Hz,  l_guard=102, total guard=205 > 128  ✗
    N=128,M=256 Δν=305 Hz,  l_guard=204, total guard=409 > 256  ✗  (the intuitive fix)
    N=M=256:    Δν=153 Hz,  l_guard=406, total guard=813 > 256  ✗  (bigger square)

Correct solution — reduce N:
    N=32, M=256: Δν=1221 Hz, l_guard=51, total guard=103 ≤ 256  ✓
    N=32, M=128: Δν=2441 Hz, l_guard=26, total guard=53 ≤ 128   ✓

---

## ν_max by frequency band and altitude (kHz)

| Band    | f_c (GHz) | h=350 km | h=550 km | h=1200 km |
|---------|-----------|----------|----------|-----------|
| L-band  | 1.5       | 38.5 kHz | 38.0 kHz | 36.3 kHz  |
| S-band  | 2.4       | 61.7 kHz | 60.8 kHz | 58.1 kHz  |
| C-band  | 5.0       | 128.4 kHz| 126.6 kHz| 121.0 kHz |
| X-band  | 10.0      | 256.9 kHz| 253.1 kHz| 242.0 kHz |
| Ku-band | 14.0      | 359.6 kHz| 354.4 kHz| 338.8 kHz |
| Ka-band | 30.0      | 770.6 kHz| 759.4 kHz| 726.1 kHz |

Note: altitude has negligible effect — v_orb varies by only ~6% from 350 to 1200 km.

---

## N_max by frequency band and bandwidth (h=550 km)

| Band    | B=10 MHz | B=20 MHz | B=50 MHz |
|---------|----------|----------|----------|
| L-band  | **131**  | 263      | 658      |
| S-band  | **82**   | 164      | 411      |
| C-band  | **39**   | 79       | 197      |
| X-band  | **19**   | 39       | 98       |
| Ku-band | **14**   | 28       | 70       |
| Ka-band | **6**    | 13       | 32       |

---

## Key findings for the thesis and Paper 4

**Finding 1 — S-band and L-band are viable at B=10 MHz:**
N_max=82 (S-band) and N_max=131 (L-band) allow practical rectangular grids
(e.g. N=32, M=256 at S-band) with good data capacity.

**Finding 2 — Ka-band at B=10 MHz is impractical (N_max=6):**
Only 6 delay bins — effectively no delay diversity. The three-function pilot frame
requires at least N ~ 10×k_guard_pos to leave meaningful data capacity. At Ka-band,
B ≥ 50 MHz is needed to achieve a usable grid (N_max=32 at B=50 MHz).

**Finding 3 — Bandwidth is the escape route at higher frequencies:**
The constraint relaxes linearly with B. A system designer choosing a higher
frequency band must compensate with proportionally wider bandwidth to maintain
the same grid capacity.

**Finding 4 — Altitude is not a significant design variable:**
v_orb varies by only ~6% across the entire LEO range (350–1200 km). Grid sizing
is essentially determined by frequency band and bandwidth alone, not by altitude.

**Finding 5 — The constraint is novel:**
Raviteja et al. (2018a, 2018b) use small illustrative grids (N=M=4 to 32) without
deriving this constraint. The implication for LEO deployment at specific frequency
bands is not discussed in the OTFS literature. This derivation is a contribution
of this thesis and should appear formally in Paper 4, Section IV.

---

## Decision

**The correct OTFS grid for full LEO ISAC at B=10 MHz is band-dependent:**

| Band    | Recommended grid | Δν    | l_guard | η_data |
|---------|-----------------|-------|---------|--------|
| L-band  | N=64, M=256     | 610 Hz| 62      | ~52%   |
| S-band  | N=32, M=256     | 1221 Hz| 51     | ~60%   |
| C-band  | N=32, M=512     | 610 Hz | 207    | needs wider B |
| X-band  | N=16, M=512     | 1221 Hz| 207    | needs wider B |

For C-band and above at B=10 MHz, a wider bandwidth is strongly recommended.
At S-band with B=10 MHz and N=32, M=256: η_data ≈ 60%, which is acceptable.

---

## Consequences

- `make_leo_pilot_frame()` raises ValueError when N > floor(B/(2×ν_max)) with a
  guidance message naming the N_max for the given parameters.
- All waveform experiments at S-band use OTFSGrid(N=32, M=256, bandwidth_hz=10e6).
- Paper 4 (IEEE Trans. Wireless Communications), Section IV: include a grid sizing
  subsection with the N_max formula, the table above, and a figure showing N_max
  vs f_c for B=10, 20, 50 MHz.
- The thesis (Chapter 4, Section 4.1) documents this as a fundamental OTFS system
  design parameter for LEO ISAC, not a limitation of the implementation.
