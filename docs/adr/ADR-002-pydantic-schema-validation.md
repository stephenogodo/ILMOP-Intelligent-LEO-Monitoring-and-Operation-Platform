# ADR-002 — Pydantic v2 for Telemetry Schema Validation

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP produces and consumes telemetry records across multiple services — simulator, Kafka, sink, detector, API, and dashboard. Each service must agree on the shape, types, and constraints of these records. Without a shared schema enforced at runtime, type mismatches between services would be silent and difficult to diagnose.

---

## Decision

Pydantic v2 is used as the schema definition and validation layer for all ILMOP data models. The `Telemetry` and `Alarm` dataclasses are Pydantic `BaseModel` subclasses defined in `shared/schemas/`.

---

## Rationale

Pydantic v2 provides runtime type validation with a performance-optimised Rust core, making it suitable for the 60 Hz telemetry stream produced under high-speed simulation. Its integration with FastAPI is native — FastAPI uses Pydantic models directly for request/response validation and automatic OpenAPI schema generation (Ramírez, 2018). This eliminates the need for separate serialisation, validation, and documentation code.

The `model_dump(mode="json")` method provides JSON serialisation suitable for Kafka message payloads without additional libraries. Pydantic's strict mode prevents silent coercion of incorrect types, which is important for satellite telemetry where a float masquerading as an integer in a battery percentage field would produce incorrect anomaly scores.

Pydantic v2's improved performance over v1 (up to 5–50x faster validation in benchmarks) was a secondary consideration given the 60 Hz per-satellite record rate under Scenario 3 high-speed operation.

---

## Consequences

**Positive:**
- Single source of truth for the `Telemetry` schema used by all seven services
- Automatic OpenAPI documentation at `/docs` with no additional code
- Runtime validation catches type errors at the service boundary
- `model_dump(mode="json")` handles datetime serialisation to ISO 8601

**Negative:**
- Pydantic v2 API is incompatible with v1; migration required if legacy code is integrated
- Strict validation adds a small latency overhead per record (~0.1 ms at estimated rates)

---

## Alternatives Considered

- **Dataclasses + marshmallow:** More flexible but requires separate validation and serialisation code
- **attrs:** Strong typing but not natively integrated with FastAPI
- **Protocol Buffers:** Binary efficiency but significant tooling overhead for a Python-native stack

---

## References

- Pydantic Documentation. (2023). *Pydantic v2 — Data validation using Python type hints*. https://docs.pydantic.dev/latest/
- Ramírez, S. (2018). FastAPI framework — Pydantic integration. https://fastapi.tiangolo.com/tutorial/response-model/
