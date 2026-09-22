# ADR-016 — Mandatory Separation of LEO and HEO Anomaly Detection Models

**Date:** 2024-03-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP operates four constellation scenarios: three LEO circular (Scenarios 1–3) and one Molniya HEO (Scenario 4). Both orbit types produce telemetry with the same schema, but their physical characteristics differ fundamentally. A decision must be made about whether to train one anomaly detection model on combined data or separate models per orbit type.

---

## Decision

LEO circular and Molniya HEO telemetry are never mixed in a single training dataset. Separate Isolation Forest models are trained, registered, and deployed for each orbit type. The `orbit_type` field in the `Telemetry` schema gates model routing in the detector.

---

## Rationale

The statistical signatures of LEO circular and Molniya HEO telemetry are fundamentally incompatible for three independently sufficient reasons:

**Reason 1 — ML training data contamination.** Eclipse fraction, battery cycling amplitude, thermal cycling range, and contact duration differ fundamentally between the two orbit types. A 550 km LEO satellite experiences ~35–40% eclipse fraction per 90-minute orbit (Larson & Wertz, 1992). A Molniya satellite experiences a ~2-hour eclipse at perigee and near-continuous sunlight during its 8-hour apogee dwell (Hoots & Roehrich, 1980). An Isolation Forest model trained on combined data would learn a confused mixture of both distributions, with each orbit type appearing anomalous relative to the other. The resulting model would produce excessive false alarms for both.

**Reason 2 — SGP4 accuracy near Molniya perigee.** SGP4 positional accuracy degrades to kilometre-level errors near Molniya perigee due to the high eccentricity (e=0.74) and the large atmospheric drag perturbation at the low perigee altitude (~500 km) (Vallado, 2013). At these accuracy levels, eclipse state determination (which requires sub-kilometre accuracy for the shadow boundary calculation) becomes unreliable. Using LEO eclipse-physics-based anomaly detection on Molniya perigee records would create systematic false alarms regardless of training data quality.

**Reason 3 — Operational paradigm incompatibility.** LEO ground segment operations are organised around frequent short passes (~8 minutes every 95 minutes). Molniya operations are organised around long apogee dwells (~8 hours per orbit). Contact scheduling, data volume planning, and anomaly response procedures differ fundamentally between the two paradigms. A single operational model spanning both would confuse operators about which satellite type any given alarm applies to.

---

## Consequences

**Positive:**
- Each model learns a clean, physically consistent normal envelope for its orbit type
- No cross-contamination false alarms between orbit types
- Scenario 4 anomaly detection (Sprint 6) can be trained independently when HEO telemetry is available
- `orbit_type` gating in the detector provides automatic routing without operator configuration

**Negative:**
- Scenario 4 requires a separate training campaign before HEO anomaly detection is available
- Training pipeline must enforce the orbit type filter — accidental mixed training produces a silently degraded model

---

## Alternatives Considered

- **Single model with orbit_type as a feature:** Would allow the model to learn orbit-type-conditional normal envelopes, but the two distributions are so different that a single model would need far more training data to learn both and would still produce transition-zone false alarms
- **Orbit-type-stratified sampling:** Train on equal proportions of each orbit type; still produces confused baseline distributions

---

## References

- Hoots, F. R., & Roehrich, R. L. (1980). *Spacetrack Report No. 3: Models for propagation of NORAD element sets*. Aerospace Defense Command.
- Larson, W. J., & Wertz, J. R. (Eds.). (1992). *Space Mission Analysis and Design* (2nd ed.). Microcosm Press.
- Vallado, D. A. (2013). *Fundamentals of Astrodynamics and Applications* (4th ed.). Microcosm Press.
