# ADR-007: Eclipse-aware battery and thermal physics models

**Status:** Accepted
**Sprint:** 2
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 1's battery and thermal models used independent random walks:
battery percentage changed by a random amount each tick; temperature changed by a random amount each tick. The five fields
`solar_panel_power_w`, `cpu_utilization_pct`, `memory_utilization_pct`, `downlink_rate_mbps`, and `uplink_rate_mbps` were hardcoded constants
that never varied.

This produced telemetry with two critical deficiencies:

**Statistical deficiency:** In real LEO operations, battery and temperature are not independent random processes — they are strongly
driven by the eclipse cycle. Battery discharges when the satellite is in Earth's shadow (no solar power) and charges in sunlight. Temperature
drops in eclipse (no solar heating) and rises in sunlight (solar flux plus Earth albedo). A random walk model produces the correct *range* of
values but loses all *correlation structure*.

**Consequence for anomaly detection (Sprint 5):** An anomaly detection
model trained on random-walk telemetry learns nothing useful. There is no normal pattern to learn — the data is structureless noise. A model
trained on eclipse-correlated telemetry learns that battery SoC should be recovering in sunlight and declining in eclipse; that temperature should be cycling with the orbital period; that solar panel power should be zero in eclipse. Deviations from these patterns are the anomalies.
The physics model is not cosmetic — it is the prerequisite for meaningful AI/ML.

---

## Decision

Battery and thermal models are replaced with eclipse-aware first-order physics:

**Battery:** discharge rate of −0.25%/tick in eclipse, charge rate of +0.15%/tick in sunlight, with small Gaussian noise (σ=0.05%). Solar panel power output: 0 W in eclipse, Gaussian(μ=1300 W, σ=20 W) in sunlight.

**Thermal:** first-order lag toward −20°C (eclipse equilibrium) or +35°C (sunlight equilibrium) with gain α=0.008 per tick and Gaussian noise (σ=0.15°C).

**New dynamic fields:** `solar_panel_power_w` (eclipse-gated), `cpu_utilization_pct` (random walk with contact-period spike),
`memory_utilization_pct` (slow random walk), `downlink_rate_mbps` and `uplink_rate_mbps` (gated by `_ContactModel` state machine).

All models receive the `in_eclipse` boolean from `OrbitModel` as their primary input, creating the correlation structure that real satellite
telemetry exhibits.

---

## Alternatives considered

### Retain random walk models
Keep the Sprint 1 random walk models and add eclipse-gating only to
the solar panel power field.

**Rejected because:** partial eclipse-awareness is worse than full eclipse-awareness from a training data perspective. If battery and
temperature still random-walk independently, the anomaly detection model must learn to ignore the battery/thermal correlation with eclipse — but
it will fail to do so cleanly because the partial signal is misleading. Either the full causal structure is modelled or it is not.

### Full thermal engineering simulation (ESATAN-TMS, Thermal Desktop)
Industry-standard spacecraft thermal analysis tools that model radiative heat transfer, conduction, thermal mass, surface optical
properties, and the full solar/albedo/IR environment.

**Rejected because:** these tools require spacecraft CAD geometry, material properties, and optical surface characterisations that do not
exist for a simulated spacecraft. They are appropriate for flight hardware design validation, not for generating representative telemetry training data. The first-order lag model captures the dominant physics (the orbital
eclipse cycle) with parameters that produce a realistic temperature range (−20°C to +35°C equilibrium) and correct thermal inertia timescale (hundreds of seconds), which is sufficient for anomaly detection training.

### Lookup tables from real satellite telemetry
Use actual historical telemetry from a real LEO satellite (if available) to parameterise the models, rather than analytical physics models.

**Not applicable currently:** no real satellite telemetry dataset is available to the project. This approach is worth pursuing in the PhD
integration phase — the OFDM payload, once demonstrated, could provide real telemetry against which the physics models are calibrated. Model calibration from real data is the long-term goal; analytical models are the bootstrap.

---

## Rationale

The eclipse-aware models are the minimum viable physics that produce telemetry with the correlation structure needed for anomaly detection:

**Battery physics:**
- Eclipse: satellite runs on stored energy → SoC decreases
- Sunlight: solar panels generate more power than the bus consumes → SoC increases
- This is not an approximation — it is the dominant driver of battery state in LEO. The values (0.25%/tick discharge, 0.15%/tick charge)
  are consistent with a typical smallsat power budget at 1 Hz cadence.

**Thermal physics:**
- Eclipse: no solar flux, satellite radiates to deep space (~4 K effective temperature) → temperature decreases
- Sunlight: solar constant (~1361 W/m²) plus Earth albedo heating → temperature increases
- First-order lag is the correct analytical model for a lumped thermal mass with constant heating/cooling input. The targets (−20°C, +35°C)
  and gain (α=0.008/tick) produce a thermal swing of ~10–15°C per orbit at 1 Hz cadence — physically reasonable for a smallsat bus.

**Test validation:** `test_thermal_swing_over_orbit()` confirms a
measured swing ≥10°C over a simulated orbit, and
`test_eclipse_consistent_with_solar_power()` confirms zero solar power
during eclipse — both passed in the Sprint 2 test suite (43/43).

The `_ContactModel` state machine for link rates replaces the hardcoded zero values with realistic burst behaviour (5–10 minute contact windows with 50–100 minute gaps), which is critical for the CPU utilisation model (CPU spikes during contact) and for the link rate statistics that feed into capacity planning and communication performance analysis.

---

## Consequences

### Positive
- Telemetry exhibits the eclipse-correlated structure that real LEO satellite telemetry shows — making it suitable for anomaly detection
  model training
- `solar_panel_power_w` is now physically meaningful: exactly 0 W in
  eclipse, ~1300 W in sunlight — a strong, testable invariant 
  - Thermal cycling with the orbital period is observable in stored telemetry — enables the "trend analysis" features planned for Sprint 4
- All five previously hardcoded fields now vary — training data has full-dimensional variance across all 17 telemetry fields 

### Negative / trade-offs
- Models are simplified — they do not account for battery degradation
  over charge cycles, variable solar flux with orbital geometry, or thermal gradients within the spacecraft
- The `_ContactModel` timer is not ground-station-aware — it generates
  statistically realistic pass durations and gaps but not geometrically  correct ones; this is replaced in Sprint 6 by the ground station
  scheduler 
  - Physics model parameters (discharge rate, thermal gain, etc.) are not
  calibrated against a specific spacecraft — they are representative values for a generic smallsat bus

### Implications for future sprints
- Sprint 5 (anomaly detection): the Isolation Forest model trains on
  correlated telemetry; synthetic faults (battery that fails to charge in sunlight, temperature that fails to recover from eclipse) are now detectable as deviations from the learned eclipse-correlated normal
- Sprint 6 (ground station scheduler): `_ContactModel` is replaced by a geometry-driven contact window calculator using the SGP4 orbit and configured ground station positions — link rates become geometrically correct, not statistically approximated

- PhD integration: the battery discharge/charge pattern in the telemetry directly reflects the eclipse flag, which is derived from SGP4 orbit
  truth — the navigation demonstration can use battery state transitions as a proxy for eclipse boundary crossing, validating the waveform's timing accuracy
