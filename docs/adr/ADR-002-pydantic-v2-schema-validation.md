# ADR-002: Pydantic v2 for telemetry schema validation

**Status:** Accepted
**Sprint:** 1
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

ILMOP's telemetry pipeline connects six components across three sprint phases: satellite simulator, Kafka producer, Kafka consumer, TimescaleDB sink, FastAPI REST layer, and Streamlit dashboard. Each component reads
or writes a `Telemetry` record. Without a single, enforced schema definition, a field rename in the simulator silently produces malformed records that propagate through the pipeline undetected — causing database write failures, API errors, or silent corruption in the dashboard.

A schema validation library must:
1. Define the canonical `Telemetry` type in one place
2. Validate records at runtime, not just at static analysis time
3. Serialise cleanly to JSON for Kafka transport
4. Integrate with FastAPI's automatic OpenAPI documentation (Sprint 4)
5. Produce clear, actionable error messages when validation fails

---

## Decision

Pydantic v2 (`pydantic>=2.0`) is the schema validation library for all ILMOP data models, beginning with `shared/schemas/telemetry_schema.py`. All inter-service data contracts are defined as Pydantic `BaseModel` subclasses.

---

## Alternatives considered

### Python dataclasses (stdlib)
Available without any additional dependency. Supports type annotations
and `@dataclass` decorator. Used by `services/satellite_simulator/ satellite.py` as the mutable state container.

**Rejected for schema validation because:** dataclasses perform no runtime validation. `battery_pct: float = 95.0` does not prevent
assigning a string or `None` to the field at runtime. In a multi-service pipeline where records cross process boundaries (serialised to JSON, transmitted over Kafka, deserialised in a consumer), type safety at the
boundary is essential. Dataclasses are retained for the `Satellite` mutable state object where validation is not needed and performance matters.

### attrs
A third-party library that pre-dates Pydantic and solves similar problems.
Supports validators, converters, and slots for memory efficiency.

**Rejected because:** attrs has no built-in JSON serialisation (requires `cattrs` separately), no native FastAPI integration, and its validator
syntax is more verbose than Pydantic's. The FastAPI integration is a decisive factor: FastAPI uses Pydantic models natively for request/response schemas and automatic OpenAPI generation. Using attrs would require a
translation layer at the API boundary.

### marshmallow
A serialisation and validation library with a long track record in Django/Flask ecosystems.

**Rejected because:** marshmallow requires separate schema class definitions alongside the data class definitions (schema and model are
separate objects). Pydantic unifies them. marshmallow also has no native FastAPI integration and is significantly slower than Pydantic v2's Rust-backed core.

### msgspec
A newer, very high-performance serialisation library with runtime type checking. Significantly faster than Pydantic for pure serialisation benchmarks.

**Rejected because:** msgspec has no FastAPI integration at the time of this decision, and its ecosystem maturity (documentation, community,
third-party integrations) is lower than Pydantic's. Performance is not the binding constraint for ILMOP's telemetry pipeline — the bottleneck is Kafka I/O and database writes, not in-process validation. 
msgspec is worth reconsidering if profiling reveals validation as a hotspot in Sprint 5+.

---

## Rationale

Pydantic v2 satisfies all five requirements stated in the context:

1. **Single definition:** `class Telemetry(BaseModel)` in `shared/schemas/telemetry_schema.py` is the one canonical definition imported by every service
2. **Runtime validation:** assignment of wrong types raises  `ValidationError` immediately, not silently
3. **JSON serialisation:** `model.model_dump(mode='json')` produces a JSON-serialisable dict with `datetime` fields converted to ISO 8601
   strings — exactly what the Kafka producer needs
4. **FastAPI integration:** FastAPI uses Pydantic models natively; Sprint 4's REST layer will use `Telemetry` as its response model with zero additional code
5. **Error quality:** Pydantic's `ValidationError` specifies exactly which
   field failed, what value was provided, and what was expected Pydantic v2's Rust-backed core (`pydantic-core`) is approximately
5–50× faster than v1 for validation, making it appropriate even if ILMOP scales to high-frequency multi-satellite telemetry ingestion.

The schema versioning discipline is also important: the `Telemetry` schema is the contract between all services. When a new field is added (as `in_eclipse` and `in_contact` were in Sprint 2), Pydantic's `BaseModel` makes that change explicit and detectable by every consumer that imports the schema. This is the same schema evolution discipline that the Sprint 5 MLflow integration will depend on — every model artifact must record which `Telemetry` schema version it was trained against.

---

## Consequences

### Positive
- Single source of truth for the telemetry data contract across all six pipeline components
- Schema violations caught at process boundaries, not silently propagated
- FastAPI integration in Sprint 4 requires no additional serialisation code — the Pydantic model is the API response schema
- `model_dump(mode='json')` handles `datetime` → ISO 8601 conversion automatically, eliminating a common source of Kafka serialisation bugs
- Pydantic v2's performance is sufficient for all planned ILMOP scale
  targets (single satellite to small constellation)

### Negative / trade-offs
- Adds a non-stdlib dependency (`pydantic`, `pydantic-core`)
- Pydantic v2 introduced breaking changes from v1; any third-party
  library that pins `pydantic<2` will cause dependency conflicts - Schema changes that remove or rename required fields are breaking
  changes for all consumers — requires coordinated deployment or a schema registry (Sprint 4 consideration)

### Implications for future sprints
- Sprint 3 (TimescaleDB sink): the sink consumer validates each Kafka message through `Telemetry(**json.loads(msg))` before writing to the
  database — schema violations become database write errors rather than silently corrupt rows
- Sprint 4 (FastAPI): `Telemetry` becomes the FastAPI response model with no additional work; OpenAPI documentation is generated automatically
- Sprint 5 (MLflow): each MLflow model run must log the current  `Telemetry` schema version as a tag — the schema version is the coupling between the data pipeline and the ML model
