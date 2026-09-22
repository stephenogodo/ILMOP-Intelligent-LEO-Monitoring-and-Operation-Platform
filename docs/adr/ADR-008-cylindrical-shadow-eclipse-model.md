# ADR-008 — Cylindrical Shadow Model for Eclipse Detection

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

The eclipse-aware physics models (ADR-007) require a method to determine whether a satellite is in Earth's shadow at each simulation tick. The eclipse state drives solar panel power (0W in shadow) and is the primary driver of battery charge/discharge and thermal cycling.

---

## Decision

The cylindrical shadow model is used for eclipse detection. Earth's shadow is modelled as a cylinder with radius equal to Earth's mean equatorial radius (6,378.137 km), extending infinitely in the anti-Sun direction.

---

## Rationale

The cylindrical shadow model is the standard operational approximation for eclipse detection in LEO satellite ground segments (Vallado, 2013, Algorithm 29). It provides a closed-form, computationally trivial test: a satellite is in eclipse if its projection onto the Earth-Sun plane is within one Earth radius of the Earth-Sun axis and on the anti-Sun side of Earth.

Montenbruck & Gill (2000) demonstrate that for LEO satellites at 550 km altitude, the cylindrical model introduces a maximum eclipse fraction error of approximately 1–2% compared to the more accurate penumbra/umbra model, because the penumbra region is narrow at LEO altitudes. The penumbra transition (partial eclipse) lasts only ~2 minutes per orbit compared to a ~35-minute total eclipse duration — a 5% fraction of eclipse time.

For ILMOP's purposes, the 1–2% eclipse fraction error is operationally insignificant: the physics models use a binary eclipse state, and the anomaly detector is not sensitive to eclipse boundary events at the 1-minute timescale. The cylindrical model's computational simplicity (a single dot product test) makes it appropriate for the 1 Hz × 24 satellite simulation loop.

---

## Consequences

**Positive:**
- Computationally trivial — single vector operation per satellite per tick
- Direct implementation of Vallado (2013) Algorithm 29 ensures correctness
- 1–2% eclipse fraction error is negligible for anomaly detection purposes

**Negative:**
- Does not model penumbra (partial eclipse) — solar panel power transitions abruptly between 0W and ~1300W
- Eclipse boundary timing error of ~1–2 minutes per orbit; not relevant for the current use case

---

## Alternatives Considered

- **Penumbra/umbra dual-cone model:** More accurate eclipse boundary but computationally more complex and unnecessary for this application
- **Analytical eclipse fraction from orbital parameters:** Gives average eclipse fraction but not instantaneous state, which is required for per-tick physics

---

## References

- Montenbruck, O., & Gill, E. (2000). *Satellite Orbits: Models, Methods and Applications*. Springer.
- Vallado, D. A. (2013). *Fundamentals of Astrodynamics and Applications* (4th ed., Algorithm 29: Shadow Determination). Microcosm Press.
