# ADR-012 — FastAPI as the REST API Layer

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP requires a REST API to expose telemetry and alarm data from TimescaleDB to the Streamlit dashboard, to external clients, and to the PhD navigation validation pipeline. The API must provide automatic documentation, support Pydantic response models (already used throughout ILMOP), and perform well enough for the dashboard's 6-second polling interval.

---

## Decision

FastAPI is the REST API framework for the ILMOP data access layer (`services/api/`).

---

## Rationale

FastAPI is designed around Pydantic models and Python type hints, making it the natural choice given ILMOP's existing Pydantic schema layer (Ramírez, 2018). Endpoint request and response schemas are defined as Pydantic models, which are automatically converted to OpenAPI 3.0 (formerly Swagger) documentation at `/docs` (OpenAPI Initiative, 2021). This eliminates manual API documentation entirely.

REST (Representational State Transfer) as an architectural style (Fielding, 2000) provides the stateless, resource-oriented interface appropriate for ILMOP's read-heavy API pattern. Each endpoint addresses a specific resource — `/telemetry/{satellite_id}/latest`, `/alarms/{satellite_id}` — using standard HTTP semantics that any client can consume without prior knowledge of the system.

FastAPI's performance benchmarks place it among the fastest Python web frameworks, approaching NodeJS and Go throughput (Ramírez, 2018). For ILMOP's 6-second dashboard polling interval this is not a bottleneck, but it ensures the API does not become a constraint as the demonstration layer develops in Sprint 6.

---

## Consequences

**Positive:**
- Automatic OpenAPI 3.0 documentation at `/docs` with live request testing
- Pydantic response models provide type-safe, validated API responses
- Hot-reload via `uvicorn --reload` accelerates development iteration
- Standard REST semantics — any HTTP client can consume the API

**Negative:**
- Asynchronous architecture (FastAPI + uvicorn) requires async-aware database access; ILMOP uses a synchronous psycopg2 adapter, requiring `run_in_executor` for database calls

---

## Alternatives Considered

- **Flask:** Simpler but no built-in async support, no Pydantic integration, manual API documentation
- **Django REST Framework:** Full-featured but heavyweight for ILMOP's simple read-only endpoints
- **gRPC:** Binary efficiency but requires client code generation; incompatible with browser-based dashboard access

---

## References

- Fielding, R. T. (2000). *Architectural Styles and the Design of Network-based Software Architectures* (Doctoral dissertation, University of California, Irvine). https://www.ics.uci.edu/~fielding/pubs/dissertation/top.htm
- OpenAPI Initiative. (2021). *OpenAPI Specification 3.0.3*. https://spec.openapis.org/oas/v3.0.3
- Ramírez, S. (2018). FastAPI — Modern, fast web framework for building APIs with Python 3.7+. https://fastapi.tiangolo.com
