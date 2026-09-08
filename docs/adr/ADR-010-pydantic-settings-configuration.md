# ADR-010: pydantic-settings for centralised configuration management

**Status:** Accepted
**Sprint:** 3
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 1 and 2 services contained hardcoded configuration strings
scattered across multiple files:

- `"localhost:9092"` — Kafka bootstrap server, in both producer and consumer
- `"telemetry-topic"` — Kafka topic name, hardcoded in producer
- `"ilmop-consumer-group"` — consumer group ID, hardcoded in consumer
- `"localhost"`, `5432`, `"ilmop"` — TimescaleDB connection params,
  not yet in code but about to be needed in Sprint 3

This creates four concrete problems:

1. **Docker networking failure:** when services run inside Docker
   containers, `localhost` resolves to the container itself, not the
   host machine. The Kafka broker is reachable as `kafka` (its Docker
   service name), not `localhost`. Hardcoded strings cause silent
   connection failures with no clear error message.

2. **Azure deployment breakage:** in Sprint 6, the Kafka broker becomes
   an Azure Event Hubs endpoint and TimescaleDB becomes an Azure
   Database for PostgreSQL hostname. Updating these requires finding and
   editing every file that contains the string.

3. **No environment separation:** there is no mechanism to run the same
   codebase against a local development broker and a production broker
   without editing source files.

4. **No validation:** a misconfigured port (a string `"5432x"` instead
   of integer `5432`) is only detected at runtime when the connection
   attempt fails, not at startup.

A configuration system must:
1. Define all settings in one place
2. Read values from environment variables (enabling Docker and Azure
   override without code changes)
3. Validate types at startup (misconfigured values fail fast)
4. Provide sensible defaults for local development
5. Be consistent with the Pydantic ecosystem already in the stack

---

## Decision

`pydantic-settings` (`BaseSettings` subclass) is used for all ILMOP
runtime configuration. A single `Settings` class in `shared/config.py`
defines every configurable value with its type, default, and description.
A module-level `settings = Settings()` singleton is imported by every
service.

---

## Alternatives considered

### os.environ with manual defaults
```python
KAFKA_BROKER = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
```
Available with no additional dependency. Simple and transparent.

**Rejected because:** no type validation — every value is a string;
integer ports and float timeouts must be manually cast and can fail
silently. No central definition — settings are scattered across every
file that uses them. No IDE autocompletion — `os.environ.get("KAFK_...")` 
typos are not caught until runtime. No `.env` file support without
an additional library.

### python-dotenv alone
Loads a `.env` file into `os.environ` at startup. Combines with
`os.environ.get()` calls throughout the codebase.

**Rejected because:** same problems as `os.environ` — no type validation,
no central definition, no IDE autocompletion. `python-dotenv` is a
file-loading utility, not a settings management system. `pydantic-settings`
uses `python-dotenv` internally for `.env` file loading while adding
all the missing capabilities.

### configparser (stdlib INI files)
Standard library config file parser. No additional dependency.
Supports sections, fallback values, and interpolation.

**Rejected because:** INI files are not environment-variable-aware —
switching between local and Docker configurations requires maintaining
separate config files or a wrapper script. Type casting is manual.
INI file syntax is unfamiliar compared to environment variables, which
are the universal deployment configuration mechanism (Docker, Kubernetes,
Azure App Service all use environment variables natively).

### dynaconf
A full-featured configuration management library supporting multiple
file formats (TOML, YAML, INI, JSON), layered environments (development,
staging, production), and secrets management.

**Rejected because:** dynaconf's power exceeds ILMOP's requirements at
this stage. Its layered environment system introduces indirection that
makes it harder to reason about which value is active in a given
deployment context. `pydantic-settings` covers all of ILMOP's
configuration needs with less complexity.

---

## Rationale

`pydantic-settings` is the natural choice for a project already using
Pydantic v2:

- **Type validation at startup:** `timescaledb_port: int = 5432` means
  the setting is validated as an integer when `Settings()` is constructed;
  a misconfigured `TIMESCALEDB_PORT=5432x` raises a `ValidationError`
  before any service logic runs
- **Single source of truth:** every configurable value is defined once
  in `shared/config.py`; adding a new setting means adding one line, not
  searching across files
- **Environment variable override:** every field is automatically
  overrideable by its uppercase environment variable name —
  `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` overrides the `localhost:9092`
  default, enabling Docker and Azure deployment with no code changes
- **`.env` file support:** developers can create a `.env` file for local
  overrides without setting system environment variables
- **IDE autocompletion:** `settings.kafka_bootstrap_servers` is typed;
  `settings.kafk_bootstrap_servers` is a compile-time error in any
  IDE with Pydantic support
- **Helper methods:** `settings.telemetry_topic("SAT-001")` returns
  `"telemetry.SAT-001"` — topic name construction logic lives in one
  place, not duplicated across producer, consumer, and sink

The module-level singleton `settings = Settings()` means every service
imports the same object and any environment variable override is
reflected immediately at import time.

---

## Consequences

### Positive
- Docker deployment: set `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` in
  `docker-compose.yml` environment block — no code changes
- Azure deployment (Sprint 6): set connection strings as Azure App
  Service environment variables or AKS secrets — no code changes
- Misconfigured values (wrong type, missing required value) fail at
  service startup with a clear `ValidationError`, not mid-operation
- All topic names, group IDs, and connection strings are derivable
  from `shared.config.settings` — no grep-and-replace when moving
  between environments

### Negative / trade-offs
- Adds `pydantic-settings` dependency (small: ~200 KB)
- All services must import from `shared.config` — services cannot be
  run in isolation without the `shared/` package on the Python path
  (already a requirement given the `shared/schemas/` import)

### Implications for future sprints
- Sprint 4 (FastAPI): `settings.api_host` and `settings.api_port`
  configure uvicorn; `settings.timescaledb_url` configures the asyncpg
  connection pool — both already defined in `Settings`
- Sprint 4 (Streamlit): `settings.dashboard_port` configures the
  Streamlit server
- Sprint 5 (MLflow): `mlflow_tracking_uri` added to `Settings` — the
  MLflow server URL is configurable between local file store and
  Azure Blob Storage without code changes
- Sprint 6 (Azure): environment variables injected via AKS secrets or
  Azure Key Vault references override all defaults — the application
  code is deployment-environment-agnostic
