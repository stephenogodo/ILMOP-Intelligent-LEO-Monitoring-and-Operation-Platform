# ADR-009: Satellite dataclass as stateful domain object

**Status:** Accepted
**Sprint:** 2
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 1 defined a `Satellite` dataclass in
`services/satellite_simulator/satellite.py` but never imported or used it. `TelemetryGenerator` constructed `Telemetry` objects directly by calling each sub-model and assembling the results:

```python
# Sprint 1 pattern (simplified)
def generate(self) -> Telemetry:
    lat, lon, alt = self._orbit.propagate()
    pct, voltage  = self._battery.update()
    temp          = self._thermal.update()
    return Telemetry(
        latitude_deg=lat,
        longitude_deg=lon,
        # ...
        temperature_c=temp,
    )
```

This pattern does not retain any satellite state between calls. The simulator has no persistent representation of "the satellite" — only a sequence of unconnected snapshots.

With Sprint 2 introducing physical correlations between subsystems (battery depends on eclipse state; thermal depends on eclipse state; CPU depends on contact state), the generator must pass intermediate values between sub-model calls within a single tick. The direct assembly pattern forces these intermediate values to be local variables that disappear after each `generate()` call, making it impossible to:
- Inspect the satellite's current state between ticks (for debugging)
- Persist satellite state across simulator restarts
- Manage a fleet of satellites by maintaining a collection of
  satellite state objects
- Detect state transitions (e.g., entering safe mode) by comparing
  current state with previous state

---

## Decision

`satellite.py` is implemented as a `@dataclass` representing the
satellite's full mutable state. `TelemetryGenerator` holds one
`Satellite` instance and mutates it each tick. At the end of each tick,
the current satellite state is snapshotted into an immutable `Telemetry`
record.

```
Satellite   = mutable, in-memory state  (what the satellite IS right now)
Telemetry   = immutable snapshot         (what was RECORDED at a point in time)
```

`TelemetryGenerator.generate()` follows a five-step pattern:
1. Update orbit → write to `self.satellite`
2. Update contact → write to `self.satellite`
3. Update battery (using `self.satellite.in_eclipse`) → write to `self.satellite`
4. Update thermal (using `self.satellite.in_eclipse`) → write to `self.satellite`
5. Snapshot `self.satellite` → return new `Telemetry` object

---

## Alternatives considered

### Dictionary as state container
Use a `dict` to hold the current satellite state between sub-model calls
within `generate()`:

```python
state = {}
state['lat'], state['lon'], state['alt'], state['in_eclipse'] = self._orbit.propagate()
state['battery_pct'], state['voltage'], state['solar_w'] = self._battery.update(state['in_eclipse'])
# ...
return Telemetry(**state)
```

**Rejected because:** a dictionary provides no type safety, no IDE
autocompletion, no documentation of which keys exist, and no ability to
add methods. Accessing `state['in_eclipse']` instead of
`satellite.in_eclipse` is functionally equivalent but harder to read,
harder to refactor, and not inspectable with standard debugging tools.
A `@dataclass` provides all the same mutability with full type annotation
and IDE support.

### Merge `Satellite` and `Telemetry` into one class
Use the `Telemetry` Pydantic model directly as the mutable state
container — update its fields in place each tick rather than creating
a new instance.

**Rejected because:** Pydantic `BaseModel` instances are designed as
immutable value objects (they validate on construction and are hashable).
While Pydantic v2 does support mutable models with `model_config =
ConfigDict(frozen=False)`, using the output schema as the internal state
container conflates two distinct concerns: the internal simulation state
(which may include derived or transient fields) and the external data
record (which is what consumers receive). Separating them means the
`Telemetry` schema can change independently of the simulator's internal
state representation.

### Functional style — pass state explicitly
Thread state through each sub-model call as an immutable value object,
returning a new state each time:

```python
state0 = initial_state
state1 = update_orbit(state0)
state2 = update_battery(state1)
state3 = update_thermal(state2)
return to_telemetry(state3)
```

**Rejected for this use case:** while functional style is clean for
pure computations, satellite simulation is inherently stateful. The
battery's SoC at tick N+1 depends on its SoC at tick N; the thermal
model carries temperature as persistent state. Creating a new state
object each tick and copying all fields produces unnecessary allocation
overhead at 1 Hz cadence. The mutable dataclass pattern is appropriate
for a simulation loop where state carries over between ticks.

---

## Rationale

The `Satellite` dataclass as stateful domain object cleanly separates
two responsibilities that are easily conflated:

**Mutable simulation state** (`Satellite`):
- Lives in memory inside `TelemetryGenerator`
- Updated every tick by each sub-model
- Carries state across ticks (battery SoC at tick 100 depends on tick 99)
- Not serialised or transmitted

**Immutable data record** (`Telemetry`):
- Created fresh each tick as a snapshot of `Satellite`
- Validated by Pydantic on construction
- Serialised to JSON and published to Kafka
- Persisted to TimescaleDB
- Transmitted to the dashboard

This separation follows the Event Sourcing pattern that Kafka itself
embodies: the satellite is the entity (mutable state); the Telemetry
is the event (immutable record of state at a point in time). Kafka
stores events, not entities.

The pattern also enables multi-satellite operation naturally: a fleet
manager holds a `dict[str, TelemetryGenerator]` keyed by satellite ID,
each with its own `Satellite` state object. The per-satellite state
is isolated by construction.

`@dataclass` is preferred over `NamedTuple` because named tuples are
immutable (cannot update individual fields in place) and over plain
class because `@dataclass` generates `__init__`, `__repr__`, and
`__eq__` automatically, matching the behaviour of `Satellite` as
a transparent state container.

---

## Consequences

### Positive
- Each sub-model reads from and writes to a shared `Satellite` state object — the data flow within `generate()` is explicit and readable
- The `Satellite` instance is inspectable between ticks for debugging:
  `print(generator.satellite.battery_pct)` gives the current state  without consuming the telemetry record
- Multi-satellite fleet management is a `dict[str, TelemetryGenerator]`  — each satellite has isolated, independent state
- The separation of `Satellite` (mutable) from `Telemetry` (immutable) maps directly to the Event Sourcing pattern and Kafka's event log semantics

### Negative / trade-offs
- One additional class to maintain alongside `Telemetry`
- Fields must be kept in sync between `Satellite` and `Telemetry` when new telemetry fields are added — a new field added to `Telemetry` must also be added to `Satellite` if it is derived from simulation state
- The dataclass has no validation — it is possible to set
  `satellite.battery_pct = -50.0` without error; validation is the responsibility of the sub-models that write to it

### Implications for future sprints
- Sprint 3 (multi-satellite): `SatelliteSimulator` is extended to hold a `list[TelemetryGenerator]`, each with its own `Satellite` object; the Kafka producer publishes to `telemetry.{satellite_id}`
  using the satellite's ID as the message key
- Sprint 5 (safe mode detection): comparing `satellite.safe_mode` before and after each tick enables transition detection — the anomaly detection service can flag the moment safe mode is entered rather than just its presence
- PhD integration: a `PayloadState` dataclass analogous to `Satellite` will hold the OFDM payload's operating mode, transmit power, and waveform configuration; snapshotted into a `PayloadTelemetry`
  Pydantic model each tick, following the identical pattern
