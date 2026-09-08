# ADR-012: FastAPI as the REST API framework

**Status:** Accepted
**Sprint:** 4
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 4 introduces the first external-facing interface to ILMOP's
telemetry data: a REST API that the Streamlit dashboard, external
scripts, and future integrations (PhD payload analytics, mobile clients)
can query. The framework chosen for this API determines the development
experience, performance characteristics, documentation quality, and
long-term maintainability of the layer that every upstream consumer
depends on.

The API must:
1. Serve async requests efficiently — the database driver (asyncpg) is
   async; a synchronous framework would block the event loop on every
   database query
2. Generate interactive API documentation automatically — essential for
   a research platform where the schema evolves sprint by sprint and
   consumers need to see the current interface
3. Validate request parameters and response models using the Pydantic
   types already in the stack
4. Integrate naturally with the existing `Telemetry` Pydantic schema
5. Be well-documented, actively maintained, and widely adopted

---

## Decision

FastAPI is the REST framework for `services/api/`. Routes are defined
with Python type annotations; request and response schemas use Pydantic
models. The application server is Uvicorn. Interactive documentation is
available at `/docs` (Swagger UI) and `/redoc` (ReDoc) automatically.

---

## Alternatives considered

### Flask
The most widely used Python web framework. Simple, flexible, and
extremely well-documented.

**Rejected because:** Flask is synchronous by default. While Flask 2.x
added async route support, its underlying WSGI model was not designed for
async-first operation — mixing sync and async code in Flask requires
careful thread management. ILMOP's database driver (asyncpg) is fully
async; a synchronous framework wastes concurrency by blocking the
thread on every database query. Flask also has no built-in request
validation or automatic API documentation — both would require additional
libraries (marshmallow or webargs for validation, flasgger for Swagger),
recreating what FastAPI provides natively.

### Django REST Framework (DRF)
A batteries-included REST framework built on Django. Mature, widely
deployed, with a large ecosystem of extensions.

**Rejected because:** Django's ORM and administration interface are
designed for relational data models with complex relationships — features
that ILMOP does not use. DRF's weight (Django settings, migrations,
models, apps) is significant overhead for what is a lightweight read-only
telemetry API. DRF is synchronous by default; Django's async support
(added in version 3.1) is partial and not yet first-class. The time
required to configure Django correctly for a minimal API exceeds the
time to build the same API in FastAPI.

### aiohttp
A low-level async HTTP client/server library. Truly async from the
ground up; used in some high-performance production systems.

**Rejected because:** aiohttp is a library, not a framework — it provides
the transport layer but no routing conventions, no dependency injection,
no automatic documentation, and no request/response validation.
Building these from scratch on top of aiohttp would produce a custom
framework rather than a maintained solution. FastAPI provides all of
these on top of the same async foundation (Starlette, which wraps
aiohttp/uvicorn).

### Litestar (formerly Starlite)
A modern, high-performance async Python API framework. Strong typing,
OpenAPI generation, dependency injection, and Pydantic v2 support.

**Considered but not chosen:** Litestar is architecturally similar to
FastAPI and would be a reasonable choice. It is not chosen because
FastAPI has a larger community, more tutorials, and more Stack Overflow
answers — which matters for a single-developer research platform.
Litestar is worth revisiting for Sprint 6 if FastAPI shows performance
limitations at constellation scale.

---

## Rationale

FastAPI is chosen because it satisfies all five requirements with zero
additional configuration:

**1. Async-first:** FastAPI is built on Starlette (an ASGI framework)
and runs on Uvicorn. Route handlers declared with `async def` run
natively on the event loop — `await pool.fetchrow(...)` does not block
other requests. At 100 concurrent dashboard clients all refreshing at
2-second intervals, an async framework handles the load without thread
pool exhaustion.

**2. Automatic interactive documentation:** declaring
`@router.get("/telemetry/{satellite_id}/latest")` with a return type
annotation automatically generates OpenAPI 3.0 JSON and renders
interactive Swagger UI at `/docs`. Every time a new endpoint is added
or a query parameter changes, the documentation updates automatically.
For a research platform where the schema evolves sprint by sprint, this
is not a convenience — it is the only practical way to keep
documentation current.

**3. Pydantic integration:** FastAPI uses Pydantic models natively for
request body validation and response serialisation. The existing
`Telemetry` Pydantic model becomes a FastAPI response model with zero
additional code. Query parameter validation (`ge=1, le=5000` on the
`limit` parameter) is expressed as function signature annotations and
enforced automatically — invalid inputs return a structured 422 response
rather than an unhandled exception.

**4. Dependency injection:** FastAPI's `Depends()` system is used to
inject the asyncpg pool (`get_pool`) and Redis client (`get_cache`) into
route handlers. This makes both dependencies mockable in tests via
`app.dependency_overrides` — the 18 API tests pass with no live database
or Redis connection by overriding these two dependencies.

**5. Lifespan context manager:** the `@asynccontextmanager lifespan`
pattern manages the asyncpg pool and Redis connection across the
application lifetime cleanly — startup creates them, shutdown closes
them, with no manual cleanup logic.

---

## Consequences

### Positive
- Fully async — every database query uses `await` without blocking
  other requests
- `/docs` (Swagger UI) and `/redoc` available immediately with no
  additional configuration — interactive documentation for every
  endpoint, parameter, and response schema
- `Telemetry` Pydantic model is the FastAPI response schema with no
  additional serialisation code
- `Depends(get_pool)` and `Depends(get_cache)` are overrideable in
  tests — all 18 API tests run without a live database
- CORS middleware enables the Streamlit dashboard (different origin)
  to call the API from a browser

### Negative / trade-offs
- ASGI startup (Uvicorn) is slightly more complex than WSGI (Gunicorn
  + Flask) for deployment — requires understanding the worker model
- FastAPI's dependency injection is powerful but unfamiliar to
  developers coming from Flask or Django
- Automatic docs generation means the API surface is always publicly
  visible — fine for development, requires `/docs` to be disabled or
  protected before Sprint 6 production deployment

### Implications for future sprints
- Sprint 5 (anomaly detection): a `POST /telemetry/{sat_id}/score`
  endpoint or a `GET /alarms/{sat_id}` endpoint is added to the same
  FastAPI application with no structural changes
- Sprint 6 (Azure): FastAPI runs as a Docker container on AKS behind
  an Azure Application Gateway; Uvicorn worker count is configured
  via environment variable; the same `shared/config.py` provides all
  connection strings
- PhD integration: a `payload` router (`GET /payload/{sat_id}/latest`,
  `GET /payload/{sat_id}/navigation`) follows the identical pattern
  as the telemetry router, serving OFDM payload telemetry through
  the same API gateway
