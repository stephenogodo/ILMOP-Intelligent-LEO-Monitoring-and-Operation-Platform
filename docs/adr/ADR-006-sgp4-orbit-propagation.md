# ADR-006 — SGP4 as the Orbit Propagation Model

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP requires a satellite orbital position model to compute the eclipse state (which drives battery charge/discharge physics) and to provide range and range-rate data for the navigation validation pipeline. The model must be accurate enough for operational simulation and capable of running at the required simulation speed (up to 60× real time for 24 satellites simultaneously).

---

## Decision

SGP4 (Simplified General Perturbations Model 4), implemented via the `sgp4` Python library (Rhodes, 2012), is the orbit propagation model for Scenarios 1–3. Scenario 4 (Molniya HEO) also uses SGP4 for operational purposes, with the accuracy limitations documented in ADR-016.

---

## Rationale

SGP4 is the universal standard for near-Earth satellite operational orbit propagation (Hoots & Roehrich, 1980; Vallado, Crawford, Hujsak, & Kelso, 2006). It is the model used by NORAD/18th Space Control Squadron to publish TLE data and is deployed operationally in every major ground segment worldwide. Its use in ILMOP ensures that the orbital mechanics are consistent with real operational practice.

The Brandon Rhodes `sgp4` Python library (Rhodes, 2012) implements the full Vallado et al. (2006) revision of the algorithm, which corrects several numerical errors in the original Hoots & Roehrich (1980) formulation. The library accepts standard TLE data and propagates to any epoch in seconds, making it suitable for the 1 Hz simulation loop.

SGP4 positional accuracy for LEO circular orbits is 100–500 metres over a 24-hour propagation window (Vallado, 2013). This accuracy is sufficient for eclipse state determination (which requires only the satellite's position relative to Earth's shadow cone) and for contact window scheduling.

For navigation validation, SGP4's 100–500 m accuracy is insufficient to serve as the ranging truth reference. ADR-017 (Sprint 6) documents the decision to use a High Precision Orbit Propagation model for navigation residual validation.

---

## Consequences

**Positive:**
- Industry-standard model consistent with real operational practice
- Runs in microseconds per propagation — no computational constraint at 60× speed
- Direct TLE input enables testing with real satellite orbital elements
- Well-validated implementation via the Rhodes library

**Negative:**
- 100–500 m positional accuracy insufficient for navigation truth reference (addressed in ADR-017)
- Accuracy degrades significantly near Molniya perigee (km-level errors) — documented in ADR-016

---

## Alternatives Considered

- **Keplerian two-body propagation:** Simpler but ignores J2 oblateness, producing incorrect eclipse cycle timing
- **Numerical integration (RK4):** Higher accuracy but computationally expensive for real-time multi-satellite simulation
- **HPOP (High Precision Orbit Propagation):** 1–10 m accuracy but requires complex force modelling; reserved for navigation truth reference in ADR-017

---

## References

- Hoots, F. R., & Roehrich, R. L. (1980). *Spacetrack Report No. 3: Models for propagation of NORAD element sets*. Aerospace Defense Command.
- Rhodes, B. (2012). *sgp4 — Python SGP4 satellite position library*. https://pypi.org/project/sgp4/
- Vallado, D. A. (2013). *Fundamentals of Astrodynamics and Applications* (4th ed.). Microcosm Press.
- Vallado, D. A., Crawford, P., Hujsak, R., & Kelso, T. S. (2006). Revisiting spacetrack report #3. *AIAA 2006-6753*. https://doi.org/10.2514/6.2006-6753
