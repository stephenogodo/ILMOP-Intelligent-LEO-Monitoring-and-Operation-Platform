# ADR-009 — Satellite State as a Pydantic Dataclass

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

Each simulated satellite has mutable physical state (battery level, temperature, position) that evolves over time and is shared across multiple model components (BatteryModel, ThermalModel, OrbitModel). The state must be serialisable to JSON for Kafka publishing and database storage, and it must be accessible as a typed Python object within the simulator process.

---

## Decision

Each satellite's instantaneous state is represented as a Pydantic dataclass (`Satellite`) that holds the full state vector. Model components read and write fields on this shared object, and the `Telemetry` Pydantic model serialises the relevant fields for publishing.

---

## Rationale

The dataclass pattern (Fowler, 2002, "Data Transfer Object") provides a typed, structured container for the satellite state without imposing behaviour. Model components (BatteryModel, ThermalModel) are pure functions that take the satellite state as input and return updated values — they do not own the state. This separation of state from behaviour makes the models independently testable and replaceable.

Pydantic v2's dataclass support provides field-level type validation at construction time and `model_dump(mode="json")` for serialisation, eliminating the need for a separate serialisation layer.

The event sourcing pattern (Fowler, 2005) was considered for state management — storing the full history of state transitions as events rather than the current state. This would provide a complete audit trail of the satellite's physical evolution, which is valuable for post-anomaly forensics. However, for Sprint 5 the operational requirement is real-time anomaly detection, not forensics, and event sourcing would add significant complexity. The `fault_injected` flag on each `Telemetry` record provides sufficient lineage for current needs.

---

## Consequences

**Positive:**
- Typed state container with runtime validation
- Model components are pure functions — trivial to test in isolation
- Direct serialisation via Pydantic without manual field mapping

**Negative:**
- Mutable shared state requires careful ordering of model updates within the `generate()` tick
- No built-in history of state transitions (event sourcing would provide this)

---

## Alternatives Considered

- **Event sourcing:** Complete state history but significantly more complex; deferred to future work
- **Immutable value objects:** Functional correctness but creates a new object per tick at 60 Hz × 24 satellites — excessive GC pressure
- **Plain dictionary:** No type safety or validation

---

## References

- Fowler, M. (2002). *Patterns of Enterprise Application Architecture*. Addison-Wesley Professional.
- Fowler, M. (2005). *Event Sourcing*. martinfowler.com. https://martinfowler.com/eaaDev/EventSourcing.html
