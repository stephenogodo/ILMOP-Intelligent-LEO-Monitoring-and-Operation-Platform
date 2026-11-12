# ADR-018 — Python over MATLAB for the OTFS Waveform Implementation

**Date:** September 2026
**Status:** Accepted
**Deciders:** Stephen Ogodo
**Context:** Sprint 6 — OTFS orbital profile exporter and channel emulator

---

## Context

Sprint 6 added two research deliverables to ILMOP: an OTFS orbital profile exporter
(S6-01) that derives Doppler-delay grids from real SGP4 trajectories, and an OTFS
channel emulator (S6-02) that validates waveform performance against theoretical FSPL and Doppler bounds. Together these layers constitute the waveform simulation component
of the PhD research into OTFS for LEO satellite integrated sensing and communications (ISAC).
OTFS waveform simulation in academic literature is almost universally implemented in
MATLAB (Raviteja et al., 2018; Hadani et al., 2017; Hong et al., 2021). The research question facing Sprint 6 was whether to follow this convention or implement in Python,
given that ILMOP is a Python-native platform. Two implementation languages were considered: Python (NumPy/SciPy) and MATLAB.

---

## Decision

Python (NumPy/SciPy) is used for the entire OTFS waveform implementation, including the delay-Doppler grid construction, ISFFT/SFFT transforms, pulse shaping, AWGN channel simulation, channel emulation against SGP4-derived Doppler profiles, and FSPL validation. No MATLAB dependency is introduced anywhere in the ILMOP codebase.

---

## Rationale

**Integration with the ILMOP platform:**
ILMOP is a seven-layer Python stack. The SGP4 orbit propagator (ADR-006), the eclipse physics models (ADR-008), the Kafka streaming layer (ADR-003), the anomaly detector (ADR-014), and the navigation truth model (ADR-017) are all Python. The OTFS exporter must consume live SatelliteState dataclasses and orbital geometry objects produced by these layers. A MATLAB implementation would require either a file-based handoff (CSV export/import at each interface) or MATLAB Engine for Python, both of which add latency, serialisation overhead, and an additional licensed dependency to what is otherwise a zero-cost, zero-licence platform.

**Licence cost and reproducibility:**
MATLAB requires a paid institutional or personal licence. The ILMOP project is intended to be reproducible by any researcher with access to standard open-source scientific Python. A MATLAB-based implementation would make the waveform layer unreproducible
without a licence, undermining the academic integrity of the contribution. Python with NumPy and SciPy is freely available under BSD licences and is the standard open-science computing stack (Virtanen et al., 2020; Harris et al., 2020).

**Windows development environment:**
ILMOP is developed on Windows (username: Admin). The MATLAB Engine for Python API has historically had compatibility issues across Windows Python versions and requires a matching MATLAB installation on the same machine. NumPy and SciPy install cleanly via pip on Windows without system-level dependencies, consistent with the project's zero-external-binary philosophy (ADR-001).

**Numerical equivalence of the transforms:**
The core OTFS operations — ISFFT, SFFT, delay-Doppler grid construction, and pulse shaping — are standard 2-D DFT operations that NumPy implements identically to MATLAB's fft2/ifft2. The AWGN channel and FSPL models are straightforward linear algebra. No
MATLAB-specific toolbox function is required; every operation has a direct, equivalent NumPy/SciPy counterpart. The Sprint 6 validation confirmed that the Python implementation matches theoretical FSPL and Doppler bounds to within numerical precision. 


**Existing codebase investment:**
By the start of Sprint 6, ILMOP had 152 passing tests across five Python services. Introducing a MATLAB dependency would have required a parallel test infrastructure or excluded the waveform layer from the pytest suite entirely, breaking the project's continuous testing discipline.

**Academic precedent for Python OTFS:**
While MATLAB dominates the published OTFS literature, Python implementations have appeared in recent open-source repositories and are increasingly accepted in signal-processing venues. The NumPy FFT routines are numerically identical to those in MATLAB, so results are portable and comparable to published MATLAB benchmarks
(Raviteja et al., 2018; Hong et al., 2021).

---

## Consequences

**Positive:**
- No licence cost — the waveform layer is fully reproducible by any researcher
- Native integration with SatelliteState, SGP4 geometry, and the ILMOP test suite
- OTFS tests included in the standard `pytest` run — no separate test runner needed
- Doppler profile derived directly from the live SGP4 propagator, not from a CSV export
- Sprint 6 validation passed: FSPL and Doppler residuals within theoretical bounds

**Negative:**
- Published OTFS MATLAB code (Raviteja et al., 2018 companion scripts) cannot be imported directly — manual translation to NumPy was required for the Sprint 6 emulator
- The PhD thesis will need to justify the Python implementation to reviewers who expect MATLAB; the numerical equivalence argument is sufficient but must be stated explicitly
- No access to MATLAB's Communications Toolbox functions (e.g. `comm.OTFSChannel`);
  all channel models are implemented from first principles

---

## Alternatives Considered

- **MATLAB with file-based handoff to Python:** 
SGP4 orbit data exported as CSV; MATLAB
  reads CSV, runs waveform simulation, exports results back to Python. Rejected —
  introduces latency, breaks the live-data pipeline, and requires a MATLAB licence on every development machine.

- **MATLAB Engine for Python:** Allows Python to call MATLAB functions directly. Rejected
  — requires a matching MATLAB installation, has version-specific compatibility issues on Windows, and adds a paid dependency to an otherwise zero-cost open-source project.

- **GNU Octave:** MATLAB-compatible open-source alternative. Rejected — Octave's
  Python interop (oct2py) is slower than native NumPy and introduces a non-standard dependency. The numerical equivalence with NumPy makes a direct Python implementation preferable to a MATLAB-compatible wrapper.

---

## References

- Hadani, R., Rakib, S., Tsatsanis, M., Monk, A., Goldsmith, A. J., Molisch, A. F., &
  Calderbank, R. (2017). Orthogonal time frequency space modulation. Proceedings of IEEE
  WCNC 2017. https://doi.org/10.1109/WCNC.2017.7925148
- Harris, C. R., Millman, K. J., van der Walt, S. J., et al. (2020). Array programming
  with NumPy. Nature, 585, 357–362. https://doi.org/10.1038/s41586-020-2649-2
- Hong, Y., Liu, F., & Yuan, J. (2021). Delay-Doppler Communications: Principles and
  Applications. Wiley-IEEE Press.
- Raviteja, P., Viterbo, E., & Hong, Y. (2018). OTFS performance on static multipath
  channels. IEEE Wireless Communications Letters, 8(3), 745–748.
  https://doi.org/10.1109/LWC.2018.2890643
- Virtanen, P., Gommers, R., Oliphant, T. E., et al. (2020). SciPy 1.0: Fundamental
  algorithms for scientific computing in Python. Nature Methods, 17, 261–272.
  https://doi.org/10.1038/s41592-019-0686-2
