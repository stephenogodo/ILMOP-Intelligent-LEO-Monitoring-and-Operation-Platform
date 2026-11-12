# ADR-016 — Mandatory Separation of LEO and HEO Anomaly Detection Models

**Date:** 2024-03-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP operates four constellation scenarios: three LEO circular (Scenarios 1–3) and one hybrid scenario (Scenario 4: six Molniya HEO plus twelve circular LEO satellites). Both orbit types produce telemetry with the same schema, but their physical characteristics differ fundamentally. A decision must be made about whether to train one anomaly detection model on combined data or separate models per orbit type.

---

## Decision

LEO circular and Molniya HEO telemetry are never mixed in a single training dataset. Separate Isolation Forest models are trained, registered, and deployed for each orbit type. The `orbit_type` field in the `Telemetry` schema gates model routing in the detector. The separation is per satellite, by `orbit_type`, and not per scenario: Scenario 4 contains both orbit types, its six HEO satellites supply the HEO model's training data and its twelve LEO satellites add to the LEO model's (see the addendum).

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

## Addendum (2026-11-11) — Scenario 4 as the hybrid HEO + LEO scenario

**Scenario structure.** ILMOP has four constellation scenarios. Scenario 4 is a hybrid: six Molniya HEO satellites plus twelve circular LEO satellites (two planes of six, 550 km, 53°). It serves two purposes. First, it supplies training data for both Isolation Forest models, each by orbit type: its HEO satellites are the source of all `HEO_MOLNIYA` telemetry and train the HEO model, and its LEO satellites add `LEO_CIRCULAR` telemetry to the LEO model's training data alongside Scenarios 1–3. Second, the whole constellation is the research scenario for paper P-S7-2, where I demonstrate composite-ILP contact scheduling and relay-and-forward of dark satellites' data through the HEO satellites. An earlier draft kept the HEO satellites in a standalone Scenario 4 and added a separate Scenario 5 for the research; I merged them into one scenario so the HEO definition is held in one place.

**Training separation is per satellite, by orbit type.** Training queries select telemetry by `orbit_type`, and every record carries the `orbit_type` of the satellite that produced it, so one scenario can supply two orbit classes without mixing them: the HEO query returns only the six Molniya satellites and the LEO query returns only LEO satellites. No per-scenario or per-satellite exclusion is needed, and the training code is unchanged. The cost is that the LEO training set now contains Scenario 4's LEO records as well as Scenario 3's, which I accept because they are the same orbit class (circular, 550 km, 53° in Scenario 4; 550 km in Scenario 3). Satellite IDs in Scenario 4 (`SAT-MA1`…`SAT-MF1`, `SAT-LA1`…`SAT-LB6`) are distinct from Scenario 3 (`SAT-A1`…`SAT-D6`), so records stay attributable to their scenario.

**HEO layout: one satellite in each of six planes, not six satellites in one plane.** In Scenario 4 I place one Molniya satellite in each of six planes (RAAN 0°, 60°, … 300°, mean anomaly 60° × plane index) rather than six satellites phased 60° apart in a single plane. I simulated both layouts with the ILMOP SGP4 model. For the polar stations the layouts are equivalent: Svalbard and Fairbanks have 100% coverage in both, and in both the loss of any one satellite leaves no gap. They differ elsewhere. At a 5° elevation mask the single-plane layout covers Lagos 78.5%, Houston 85.9% and Tokyo 90.3% of the time, against 100% for six planes, and at a 30° mask it covers Cambridge 77%, Houston 66% and Tokyo 68%, against 100%. The reason is geometric: every satellite in a single plane shares one ground track, so the apogee dwell always falls over the same two longitudes, whereas each 60° of RAAN moves the dwell to a different longitude. The layout also matters for the relay experiments. The fraction of dark LEO satellites that can relay their data to a ground station through the HEO satellites (at most three ISL hops) is 89.3% for the six-plane layout, and it stays within 0.5 percentage points when the whole HEO set is rotated in RAAN. For a single plane the same figure ranges from 82% to 96% depending only on the plane's RAAN relative to the LEO planes, whose nodal regression of about 4.5° per day sweeps that relative geometry through all values over a multi-day run. The mean of the single-plane figure over RAAN (about 88%) is no better than the six-plane value, so the six-plane choice does not raise the average relay performance. It removes an arbitrary experimental parameter, so that a reported result depends on the architecture and not on the epoch or RAAN chosen for the run. It also leaves the number of ground-station contact opportunities unchanged (about 26 concurrent satellite-station pairs in both layouts). The cost is that a real system of this kind would need six launch planes rather than one; for a simulation and a scheduling study I accept that cost. The six-plane layout needs a per-plane mean-anomaly offset in the YAML loader (`mean_anomaly_offset_deg`), because a plane with one satellite would otherwise start at mean anomaly 0°. That offset is essential: with every mean anomaly left at 0°, coverage of Svalbard falls to about 88%, and a regression test guards this.

---

## References

- Hoots, F. R., & Roehrich, R. L. (1980). *Spacetrack Report No. 3: Models for propagation of NORAD element sets*. Aerospace Defense Command.
- Larson, W. J., & Wertz, J. R. (Eds.). (1992). *Space Mission Analysis and Design* (2nd ed.). Microcosm Press.
- Vallado, D. A. (2013). *Fundamentals of Astrodynamics and Applications* (4th ed.). Microcosm Press.
