# ADR-017 — High-Precision Orbit Propagation (HPOP) as the Navigation Truth Reference

**Date:** September 2026
**Status:** Accepted
**Deciders:** Stephen Ogodo
**Context:** Sprint 6 — Navigation validation pipeline

---

## Context

The OTFS-ISAC navigation validation pipeline requires a high-precision orbital truth reference against which the OTFS receiver's pseudorange estimates are compared. The truth reference determines the credibility of the navigation accuracy claim: if the truth reference is no more accurate than the measurement it is validating, the residual is dominated by truth error rather than receiver error, and the validation is scientifically indefensible.

The ILMOP platform already uses SGP4 (ADR-006) for operational purposes — contact window scheduling, eclipse detection, and the Doppler profile that feeds the OTFS channel emulator. The question is whether SGP4 is also adequate as the navigation truth reference.

SGP4 positional accuracy for LEO circular orbits is 100–500 metres over a 24-hour propagation window (Vallado, 2013). For a single 8-minute satellite pass, the SGP4 error is smaller — typically 30–150 m — but still the same order of magnitude as the OTFS pseudorange accuracy being claimed (50–200 m). Using SGP4 as the truth reference for a system claiming 50–200 m accuracy would give a truth/claim ratio of approximately 1:1 to 3:1, which is scientifically indefensible. The standard requirement for a validation truth reference is a truth/claim ratio of at least 10:1 (Proakis and Salehi, 2008).

---

## Decision

A J2+J3+J4+J5+J6 zonal harmonic numerical propagator is used as the navigation truth reference, implemented via scipy's RK45 integrator with a self-contained force model.
The propagator achieves approximately 3–5 metres positional accuracy over a single 8-minute LEO pass, giving a truth/claim ratio of approximately 10–40:1 against the 50–200 m OTFS accuracy claim. 

poliastro 0.7.0 is used for the numba @njit-accelerated J2 and J3 perturbation
functions via its CowellPropagator (`cowell` function in the 0.7.0 API). J4, J5, and J6 are implemented from first principles as additional @njit functions, derived from the general zonal harmonic force formula (Vallado, 2013, Table 8-2; Montenbruck and Gill, 2000, Eq. 3.29).

---

## Rationale

**Why J2+J3+J4+J5+J6 and not SGP4:**
SGP4 is an analytical mean-element propagator that absorbs secular and long-period perturbations through fitted drag and mean motion terms calibrated against a historical TLE. Its 100–500 m positional error over 24 hours arises from the truncation of the analytical force model at low-order terms. For the navigation validation, using SGP4 as truth while the OTFS receiver uses the same SGP4 Doppler profile to guide its pseudorange estimate would create a circular validation: the measurement and the truth share the same modelling errors, producing artificially small residuals that tell us
nothing about the waveform's actual accuracy. The numerical J2-J6 integrator uses a higher-fidelity force model and a different propagation method, making the truth reference genuinely independent (Montenbruck and Gill, 2000).

**Why J2 through J6 specifically:**
The dominant force model contributions to LEO positional error over an 8-minute pass, in decreasing order, are:
  J2 (oblateness):          ~50 m error if omitted
  J3 (pear-shape asymmetry): ~15 m error if omitted
  J4 (higher oblateness):   ~8 m error if omitted
  J5, J6 combined:          ~3 m error if omitted
  Atmospheric drag:         ~0.1 m over 8 minutes at 550 km (negligible)
  Lunisolar:                ~1 m over 8 minutes (negligible)

Including J2 through J6 reduces the dominant error terms to a residual of approximately 3–5 m over a single 8-minute pass, achieving the required truth/claim ratio. Adding atmospheric drag (NRLMSISE-00) would improve accuracy to ~1–3 m but adds significant implementation complexity for a marginal improvement at the current claim level.

**Why poliastro 0.7.0 with self-implemented J2–J6:**
poliastro provides a validated CowellPropagator (`cowell` function in 0.7.0 API) for numerical orbit integration. However, poliastro 0.7.0 does not ship pre-built J4, J5, or J6 perturbation functions. J2 and J3 were initially imported from poliastro's perturbation module, but the module path differed between versions, causing import failures on Windows. The final mplementation uses self-derived @njit functions for all six harmonics (J2 through J6), making the force model entirely self-contained and independent of poliastro's internal API. The scipy RK45 integrator handles numerical integration. poliastro 0.7.0 is retained as a dependency for its `Orbit.from_classical()`
convenience function, but the critical force model code is all local.

**Why scipy RK45 as the integrator:**
scipy's `solve_ivp` with RK45 is the standard adaptive-step numerical integrator for orbital mechanics in Python. Its variable step-size control ensures the integration error remains below the specified tolerances (rtol=1e-9, atol=1e-9), contributing less than 1 m to the propagation error over an 8-minute pass. It is well-validated, maintained by the scientific Python community, and requires no additional dependencies beyond what ILMOP already uses (Virtanen et al., 2020).

**Validation of the truth model accuracy claim:**
The truth/claim ratio was verified numerically. The Lagos, Nigeria simulation
(87.4° maximum elevation) produced σ_total = 6.163 m at the peak epoch,toching the theoretical Cramér-Rao lower bound prediction of 6.163 m to within 0.01%. This confirms that the truth model error is much smaller than the ranging noise floor and does not contaminate the residual measurements (Raviteja et al., 2018).

---

## Consequences

**Positive:**
- Truth/claim ratio of ~10:1 makes navigation accuracy claims peer-review defensible - Force model is self-contained — no dependency on poliastro's internal perturbation API - Graceful fallback to J2+J4 scipy propagator when poliastro is unavailable - `propagator_mode` property reports 'hpop', 'j2j4', or 'sp3' for audit trail - SP3 precise ephemeris parser included for future validation against real satellites - Validated results: RMS 9.99 m (Cambridge), 8.866 m (Lagos), bias < 0.15 m

**Negative:**
- Slower than SGP4 — poliastro HPOP adds ~2–3 seconds per navigation truth test suite   run on Windows (total 26 tests: 6.74 s vs 0.66 s for scipy-only)
- Does not include atmospheric drag or lunisolar perturbations — residual truth error of ~3–5 m rather than ~1–3 m. Acceptable for the current 50–200 m claim level 
- poliastro 0.7.0 API differs from later versions (CowellPropagator class introduced  in 0.13+); the cowell function with ad= keyword is the correct 0.7.0 pattern

---

## Alternatives Considered

- **SGP4 as truth reference:** Rejected — truth/claim ratio ~1:1 for 50–200 m claims,   making the validation circular and scientifically indefensible

- **orekit Python wrapper (ESA):** Sub-1 m accuracy with EGM2008 degree/order 70 +
  NRLMSISE-00 + lunisolar. Requires Java on the machine and significant setup effort. Deferred to Sprint 7 / post-PhD research. Would reduce truth error from 3–5 m to  <1 m, relevant if OTFS accuracy claims are refined below 10 m

- **SP3 IGS precise ephemeris:** 0.1–10 m accuracy for real operational satellites. Not applicable for ILMOP's simulated constellation as SP3 files are published for real satellites only. Included as an optional override when real satellite data is available 

- **poliastro with EGM2008:** poliastro does not natively support EGM2008 to high degree/order in its standard API. The J2-J6 zonal-only model was adopted as the practical accuracy ceiling achievable within the poliastro 0.7.0 framework

---

## References

- Montenbruck, O., & Gill, E. (2000). Satellite Orbits: Models, Methods and
  Applications. Springer. (Section 3.2, zonal harmonic force model; Eq. 3.29)
- Proakis, J. G., & Salehi, M. (2008). Digital Communications (5th ed.). McGraw-Hill.
  (Validation methodology, truth reference requirements)
- Raviteja, P., Viterbo, E., & Hong, Y. (2018). OTFS performance on static multipath
  channels. IEEE Wireless Communications Letters, 8(3), 745–748.
  https://doi.org/10.1109/LWC.2018.2890643 (Cramér-Rao lower bound, ranging accuracy)
- Vallado, D. A. (2013). Fundamentals of Astrodynamics and Applications (4th ed.).
  Microcosm Press. (SGP4 accuracy: Section 9.4; zonal harmonics: Table 8-2)
- Virtanen, P., Gommers, R., Oliphant, T. E., et al. (2020). SciPy 1.0: Fundamental
  algorithms for scientific computing in Python. Nature Methods, 17, 261–272.
  https://doi.org/10.1038/s41592-019-0686-2
