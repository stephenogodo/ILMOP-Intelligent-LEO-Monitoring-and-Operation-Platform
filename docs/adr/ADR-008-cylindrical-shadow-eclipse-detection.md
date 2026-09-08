# ADR-008: Cylindrical shadow model for eclipse detection

**Status:** Accepted
**Sprint:** 2
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Eclipse detection — determining whether the satellite is in Earth's shadow at a given moment — is required by the battery, thermal, and
solar panel models introduced in Sprint 2. The detection method must:

1. Accept the satellite's ECI position vector (from SGP4) as input
2. Produce a binary `in_eclipse: bool` output
3. Be accurate enough that eclipse boundaries (AOS/LOS of solar illumination) are correctly timed to within a few minutes
4. Be implementable without additional library dependencies
5. Run fast enough for 1 Hz real-time simulation

The Sun's position is also needed as an input, since eclipse is defined by the geometric relationship between satellite, Earth, and Sun.

---

## Decision

A **cylindrical shadow model** using a **low-precision Vallado Sun ephemeris** is implemented in `OrbitModel._in_eclipse()` and
`OrbitModel._sun_eci_km()`.

The model:
1. Computes the Sun's ECI position using the Vallado (2013) low-precision formula (accurate to ~1° in ecliptic longitude)
2. Determines whether the satellite lies within the right circular cylinder cast by Earth blocking the Sun
3. Eclipse condition: satellite is on the anti-Sun side (dot product of satellite ECI position with Sun unit vector < 0) AND the perpendicular distance from the shadow axis is less than Earth's radius (6371 km)

---

## Alternatives considered

### Conical umbra/penumbra model (Vallado full shadow model)
A more accurate model that distinguishes between:
- Umbra: satellite is fully shadowed (in the narrow cone where the Sun is entirely blocked by Earth)
- Penumbra: satellite is partially shadowed (in the wider cone where the Sun is only partially blocked)
- Full illumination: outside both cones

This requires computing the angular size of the Sun from the satellite's perspective and solving a more complex geometric intersection.

**Rejected because:** for ILMOP's purposes, the penumbra transition —
which lasts approximately 20–40 seconds at LEO orbital velocities —
is not significant. The battery and thermal models respond at 1 Hz cadence with continuous rates; a 30-second ambiguous transition to
either full-on or full-off does not materially affect the simulated telemetry. The cylindrical model is simpler to implement, verify, and
explain, with no meaningful loss in simulation fidelity.

### Exact JPL ephemeris for Sun position (via Skyfield or SPICE)
High-precision Sun position from the JPL Development Ephemeris (DE430 or similar), accurate to metres over centuries.

**Rejected because:** the Vallado low-precision formula is accurate
to ~1° in ecliptic longitude, corresponding to a timing error of approximately 2.4 minutes in eclipse entry/exit (1° of Sun motion takes
~24 hours; the shadow geometry error is much smaller than the angular error because Earth's shadow is large). For a 95.5-minute orbit, a 2-
minute timing error in eclipse boundary is less than 2% of the orbital period — entirely acceptable for simulation. The Vallado formula requires no external data files, no network access, and no additional library
dependencies.

### Lookup table from pre-computed eclipse schedule
Pre-compute eclipse windows for the simulated orbit and store them as a table of (start_time, end_time) pairs; look up the current time against this table.

**Rejected because:** the lookup table must be regenerated whenever the orbital epoch, RAAN, or inclination changes. It also cannot handle the
case where the simulation is run at an arbitrary time not covered by the pre-computed schedule. The analytical computation is instantaneous (microseconds) at each tick, making the lookup table a complexity cost
without a performance benefit.

---

## Rationale

The cylindrical shadow model is the standard first-order approximation used in spacecraft operations when penumbra effects are not being studied:

**Geometric derivation:**
```
Let:
  s_hat = unit vector from Earth to Sun (ECI)
  r_sat = satellite ECI position vector
  d     = dot product: r_sat · s_hat  (projection along Sun direction)
  p²    = |r_sat|² − d²               (perpendicular distance² from shadow axis)
  R_e   = 6371 km (Earth radius)

Eclipse if: d < 0  (satellite on anti-Sun side)
       AND: p² < R_e²  (within the shadow cylinder)
```

This is a direct geometric test with no approximation beyond the
cylindrical assumption. The condition `d < 0` eliminates all satellites
on the illuminated hemisphere with zero additional computation.

**Validation:** The empirical eclipse fraction test
(`test_eclipse_fraction_over_one_orbit`) confirms 33.3% eclipse fraction
for a 550 km 51.6° orbit in August 2026. The theoretical eclipse fraction
for this geometry is approximately 33–36%, confirming the model is
physically correct. The test passes as part of the 43-test Sprint 2 suite.

**Vallado Sun ephemeris accuracy:**
The low-precision formula (Vallado, 2013, Algorithm 29) uses Julian centuries from J2000 to compute the Sun's ecliptic longitude and convert to ECI. The accuracy of ~1° in ecliptic longitude translates to an error
of approximately 2–3 minutes in eclipse boundary timing for a LEO orbit,
which is acceptable for simulation purposes.

---

## Consequences

### Positive
- Eclipse detection implemented with no additional library dependencies beyond `sgp4` (already required for orbit propagation)
- Instantaneous computation at each tick: the geometric test is O(1) with a handful of multiplications and additions
- Empirically validated against theoretical eclipse fraction (33.3% measured vs 33–36% expected)
- Deterministic: same UTC time always produces the same eclipse flag — essential for reproducible testing
- `_sun_eci_km()` and `_in_eclipse()` are private methods, cleanly
  encapsulated within `OrbitModel`

### Negative / trade-offs
- Ignores penumbra (partial eclipse): the ~30-second penumbra transition
  is treated as immediate full eclipse or full illumination
- Cylindrical approximation slightly overstates eclipse duration by
  ignoring the ~0.5° angular diameter of the Sun (the shadow cone's taper); the error is small (<1 minute per orbit) and consistent
- Earth is modelled as a sphere (radius = 6371 km); the actual oblateness causes negligible eclipse boundary error at LEO - Sun position accurate to ~1°; for precision solar array pointing or solar radiation pressure modelling, a higher-precision ephemeris would be needed

### Implications for future sprints
- Sprint 5 (anomaly detection): `in_eclipse` is a key feature in the telemetry record; the model learns that battery discharge correlates
  with eclipse; anomalies are deviations from this pattern - Sprint 6 (ground station scheduler): the same `_sun_eci_km()` and
  geometric framework can be extended to compute the satellite's solar beta angle (the angle between the orbital plane and the Sun vector),
  which determines maximum eclipse fraction and is used in power budget planning
- PhD integration: eclipse boundary timing from the cylindrical model can be used as a reference event for validating the OFDM waveform's
  navigation timing accuracy — if the waveform can detect the eclipse boundary through a power signature in the signal, the navigation
  residual at that moment validates the ranging solution
