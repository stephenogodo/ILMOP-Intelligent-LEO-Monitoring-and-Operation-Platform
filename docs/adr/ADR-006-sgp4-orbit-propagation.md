# ADR-006: SGP4 for satellite orbit propagation

**Status:** Accepted
**Sprint:** 2
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 1's orbit model used a mathematical shortcut: latitude and longitude were computed as `math.sin` and `math.cos` of wall-clock
seconds. This produced oscillating coordinates with approximately correct range but no physical meaning. Specifically:

- The position repeated every 86,400 seconds (one solar day) rather than every ~5,731 seconds (one LEO orbital period)
- Latitude never exceeded the hardcoded amplitude, regardless of inclination
- Longitude did not account for Earth's rotation relative to the inertial frame
- Eclipse detection was impossible because the satellite's true position relative to the Sun was unknown - Two "different" satellites would follow identical ground tracks

The Sprint 2 orbit model must produce:
1. Physically correct latitude bounded by the orbital inclination (±51.6° for an ISS-class orbit)
2. Correct orbital period (~95.5 minutes for 550 km altitude)
3. Longitude that accounts for Earth's rotation (the ground track drifts
   westward by ~23° per orbit)
4. An eclipse flag derived from the satellite's actual position relative to the Sun
5. Deterministic output for a given input time (essential for testing)
6. Results consistent with industry-standard ground software

---

## Decision

The `sgp4` Python library (Brandon Rhodes, version 2.27) is used for
orbit propagation. A satellite object is initialised from orbital elements using `Satrec.sgp4init()` with parameters representing a
550 km, 51.6° inclination orbit (ISS-class LEO). Position is computed in the ECI (Earth-Centred Inertial) frame and converted to geodetic
coordinates (lat, lon, alt) via a Greenwich Sidereal Time rotation.

---

## Alternatives considered

### Simple circular orbit — analytic Keplerian (extended)
Extend the existing `math.sin`/`math.cos` approach with correct orbital
mechanics: proper period, inclination rotation matrix, RAAN (Right Ascension of Ascending Node), and Earth rotation correction.

This would produce correct circular orbit ositions without any external library dependency.

**Partially viable but rejected** in favour of SGP4 because: a clean Keplerian model ignores all orbital perturbations — Earth's oblateness
(J2 effect), atmospheric drag, and lunisolar gravity. J2 causes the orbital plane to precess at approximately −6.9°/day for a 550 km 51.6°
orbit; a Keplerian model accumulates positional error of hundreds of
kilometres per day. More importantly, using a non-standard propagator means ILMOP's orbit model cannot be cross-validated against Space-Track or CelesTrak — the outputs are not comparable. For a platform designed
to eventually interface with real satellite operations, using the industry-standard propagator from day one is the correct decision.

### Skyfield (Python astronomy library)
A high-level Python library for astronomical computations including satellite position from TLE, Sun/Moon ephemerides, and observer-based
rise/set calculations.

**Rejected because:** Skyfield requires downloading data files from remote servers at runtime (Earth orientation parameters, JPL ephemerides)
— an unacceptable dependency for a simulation environment that must work offline. Its API is higher-level than needed: ILMOP needs raw ECI position vectors and Julian dates, not rise/set times or topocentric coordinates.
Skyfield wraps the same `sgp4` library internally; using `sgp4` directly is lighter and more appropriate.

### Numerical integration (Runge-Kutta on full equations of motion)
Integrate Newton's equations of motion with a full force model: Earth's gravity field (EGM2008 to degree/order 70+), atmospheric drag (NRLMSISE-
00), solar radiation pressure, and lunisolar third-body gravity.

**Rejected because:** this is High-Precision Orbit Propagation (HPOP) —
appropriate for precision orbit determination and manoeuvre planning, not for a mission operations simulation platform. HPOP requires atmospheric density models, solar flux inputs, and spacecraft macro-model parameters that are not available for a simulated satellite. The computational cost is orders of magnitude higher than SGP4. SGP4's accuracy (tens to hundreds
of metres over hours) is entirely sufficient for ILMOP's simulation purposes — the goal is realistic telemetry generation, not sub-metre
positioning.

### PyEphem
An older Python astronomy library (predecessor to Skyfield). Supports satellite position from TLE using the SGP4 algorithm.

**Rejected because:** PyEphem is in maintenance mode with no active development. Skyfield is its documented successor. Using a library in
maintenance mode for a new project creates unnecessary technical debt.

---

## Rationale

SGP4 (Simplified General Perturbations 4) is the universal standard for LEO satellite tracking:

- **Industry standard:** SGP4 is the algorithm used by Space-Track.org, CelesTrak, NASA, ESA, and every major commercial ground segment software package. All publicly available orbital data (TLEs) is intended to be used with SGP4 — using a different propagator with TLE inputs produces incorrect results.

- **Perturbation model:** SGP4 accounts analytically for J2, J3, J4 Earth oblateness effects (which cause ~2.5 km/orbit positional
  deviation if ignored) and atmospheric drag (which determines re-entry timeline and orbit evolution). These are the dominant perturbations for LEO.

- **Correct period and inclination:** a 550 km orbit with 51.6° inclination propagated by SGP4 produces a ground track that sweeps ±51.6° latitude with a ~95.5-minute period and drifts westward by ~23° per orbit due to Earth's rotation — all physically correct.

- **Eclipse detection:** SGP4 provides ECI position vectors, enabling the cylindrical shadow model to compute accurate eclipse detection. 
  The Sprint 1 sine-based model could not support eclipse detection at all.

- **Testability:** `_propagate_at(utc: datetime)` is deterministic —
  the same UTC time always produces the same position, enabling the   `test_same_time_same_result` and `test_eclipse_fraction_over_one_orbit`
  tests.

- **`sgp4init()` vs TLE strings:** orbital elements are passed directly
  to `Satrec.sgp4init()` rather than parsing TLE strings. This avoids TLE checksum computation for a synthetic orbit and makes the orbital
  parameters explicit and readable in code. The `sgp4` library supports both approaches.

The validation run confirms correctness: eclipse fraction over one simulated orbit = 33.3%, against the expected theoretical value of
~33–36% for a 550 km 51.6° orbit in August.

---

## Consequences

### Positive
- Physically correct ground track: ±51.6° latitude range, ~95.5-minute period, westward longitude drift - Eclipse detection now possible — gates battery discharge/charge, solar panel power, and thermal cycling - Cross-validatable against Space-Track and CelesTrak (same lgorithm)
- Deterministic output enables meaningful unit tests with physics assertions 
- `_propagate_at(utc)` method supports both real-time operation and
  test-time determinism

### Negative / trade-offs
- Adds `sgp4==2.27` dependency
- SGP4 accuracy degrades with age of orbital elements; for simulation this is irrelevant, but when real satellite TLEs are integrated (Sprint 6 / PhD track), TLE freshness must be managed 
- TLE epoch is fixed at 2026-08-24; as calendar time advances far beyond this date, the orbit will accumulate drag-induced decay error.
  For a simulator, this is acceptable; for operational use, TLEs must
  be refreshed regularly.

### Implications for future sprints
- Sprint 3 (TimescaleDB sink): ground truth lat/lon from SGP4 is stored alongside telemetry, enabling later comparison with navigation  solutions from the PhD waveform
- Sprint 5 (anomaly detection): eclipse flag from SGP4 is a feature in the telemetry record — the model can learn the normal eclipse-
  correlated power and thermal patterns
- Sprint 6 (ground station scheduler): the same `OrbitModel._propagate_at()` method, called across a time grid for every (satellite, ground station) pair, is the foundation of the contact window computation 
- PhD integration: `OrbitModel._propagate_at()` provides the orbit truth against which the OFDM waveform's navigation solution is
  validated; the same SGP4 ephemeris provides the bistatic geometry for remote sensing demonstration
