# ADR-010 — pydantic-settings for Environment-Based Configuration

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP services require configuration for Kafka broker addresses, database connection strings, simulation parameters, and MLflow tracking URIs. These values differ between development (localhost), CI, and production (Azure AKS). Hard-coding them creates a fragile codebase that cannot be deployed to different environments without code changes.

---

## Decision

`pydantic-settings` provides a `BaseSettings` class that reads configuration from environment variables with type validation and defaults. All ILMOP service configuration is managed through a single `shared/config.py` `Settings` instance.

---

## Rationale

The Twelve-Factor App methodology (Wiggins, 2011) identifies configuration as the primary dimension along which an application differs between deployment environments, and specifies environment variables as the correct mechanism for externalising this configuration. `pydantic-settings` implements this pattern with type safety — a `KAFKA_BOOTSTRAP_SERVERS` environment variable is validated as a string, and an invalid database URL raises a clear validation error at startup rather than a cryptic runtime failure.

The single `Settings` instance in `shared/config.py` means all services share a consistent configuration interface. Adding a new configuration parameter requires changing one file, not seven. The Sprint 7 Azure AKS migration requires only environment variable changes in the Kubernetes deployment manifests — zero application code changes — because `pydantic-settings` already reads from environment variables.

---

## Consequences

**Positive:**
- Sprint 7 Azure migration: zero application code changes required
- Type-validated configuration fails fast at startup with clear error messages
- Single configuration file for all services
- Default values enable local development without setting every variable

**Negative:**
- Sensitive values (database passwords) must be managed as Kubernetes secrets in production — not a limitation of pydantic-settings but a deployment concern

---

## Alternatives Considered

- **ConfigParser (.ini files):** No type validation; environment-specific values require file management
- **python-dotenv:** Loads .env files but no type validation or Pydantic integration
- **Hard-coded constants:** Zero flexibility; rejected immediately

---

## References

- Pydantic Settings Documentation. (2024). *pydantic-settings — Settings management using Pydantic*. https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- Wiggins, A. (2011). *The Twelve-Factor App — III. Config*. https://12factor.net/config
