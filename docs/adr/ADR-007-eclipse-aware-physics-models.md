# ADR-007 — Eclipse-Aware Physics Models for Anomaly Detection Validity

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP's anomaly detection model must learn what "normal" satellite telemetry looks like so it can identify deviations. If the training data is statistically random — battery performing a random walk, temperature varying with no physical cause — the model learns only the noise distribution and cannot generalise. For the model to learn a useful normal envelope, the telemetry must exhibit physical correlations that the model can represent.

---

## Decision

The ILMOP simulator implements eclipse-aware physics models for battery charge/discharge and spacecraft temperature. Solar panel power is set to exactly 0W when the satellite is in Earth's shadow. Battery discharges during eclipse and charges during sunlight. Temperature follows a physically correlated model driven by the eclipse state.

---

## Rationale

The physical correlation between eclipse state, solar power, battery charge/discharge, and temperature is the primary structure that makes satellite health monitoring tractable. In a real spacecraft, a battery discharging faster than expected during sunlight — when solar panels should be providing charging current — is a clear anomaly signal. An anomaly detection model that has learned this correlation can identify the deviation. A model trained on uncorrelated random data cannot.

Larson & Wertz (1992) establish that for a typical LEO satellite in a 550 km circular orbit, the eclipse fraction is approximately 35–40% per orbit, with the battery discharging during eclipse and recovering to full charge during sunlight passes within 2–3 orbits under healthy conditions. The ILMOP battery model implements this physics explicitly.

The thermal model is similarly eclipse-driven: spacecraft temperature in sunlight equilibrates toward ~35°C due to solar heating; in eclipse it cools toward ~-20°C due to radiative losses (Wertz, 1978). This thermal cycling is regular and predictable under normal conditions, making deviations (such as a steadily rising temperature regardless of eclipse state — thermal runaway) detectable.

The two engineered features `battery_solar_product` and `temp_eclipse_product` in the ML training pipeline (ADR-014) explicitly capture these cross-parameter correlations, which the Isolation Forest model would otherwise need to learn implicitly from more data.

---

## Consequences

**Positive:**
- Training data exhibits physical correlations that produce a meaningful normal envelope
- The battery–eclipse–solar correlation creates a detectable anomaly signature for battery degradation faults
- Eclipse-aware temperature cycling produces a detectable signature for thermal runaway faults
- Physically grounded model generalises better to unseen fault modes

**Negative:**
- Eclipse model requires SGP4 position to compute the shadow state — adds dependency on ADR-006
- Model must be retrained if the orbital parameters change significantly (different altitude, inclination)

---

## Alternatives Considered

- **Statistical random walk:** Simpler to implement but produces uncorrelated training data; model learns noise, not physics
- **Pre-recorded real telemetry:** Physically accurate but unavailable for 24-satellite constellations; requires operational access

---

## References

- Larson, W. J., & Wertz, J. R. (Eds.). (1992). *Space Mission Analysis and Design* (2nd ed.). Microcosm Press.
- Wertz, J. R. (Ed.). (1978). *Spacecraft Attitude Determination and Control*. D. Reidel Publishing.
