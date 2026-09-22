# ADR-011 — psycopg2 as the PostgreSQL/TimescaleDB Driver

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP's telemetry sink, alarm sink, and anomaly detection training pipeline all require direct PostgreSQL/TimescaleDB access. The driver must support parameterised queries (for SQL injection prevention), batch inserts (for throughput), and connection pooling.

---

## Decision

`psycopg2` is the PostgreSQL driver for all ILMOP database access.

---

## Rationale

`psycopg2` is the most widely deployed PostgreSQL adapter for Python, providing mature, production-tested support for the full PostgreSQL wire protocol including the extended query protocol (psycopg2 Documentation, 2024). Its parameterised query API (`cursor.execute(sql, params)`) uses server-side prepared statements via the extended query protocol, preventing SQL injection and improving performance for repeated queries.

The batch insert pattern used by the telemetry sink — `cursor.executemany()` or `extras.execute_values()` — reduces round-trip overhead significantly compared to individual inserts. At the 50-record batch size used by the sink, this reduces the per-record database overhead from ~5 ms to ~0.1 ms.

`psycopg2` is the driver underlying SQLAlchemy and most other Python PostgreSQL abstractions, which means switching to an ORM at a later stage would not require a driver change.

---

## Consequences

**Positive:**
- Extended query protocol parameterisation prevents SQL injection
- `execute_values()` batch insert for efficient high-throughput writes
- Mature, well-documented API with comprehensive error handling

**Negative:**
- Synchronous driver; for async services, `asyncpg` or `psycopg3` would be more appropriate. ILMOP's services are thread-based rather than async, making this a non-issue
- Requires libpq C library; available on all target platforms

---

## Alternatives Considered

- **asyncpg:** Faster for async workloads but requires asyncio throughout the service; not appropriate for ILMOP's synchronous architecture
- **SQLAlchemy ORM:** Adds abstraction overhead without benefit for ILMOP's straightforward SQL queries
- **psycopg3:** Successor to psycopg2 with async support; migration path for future async refactoring

---

## References

- psycopg2 Documentation. (2024). *psycopg2 — PostgreSQL adapter for Python*. https://www.psycopg.org/docs/
- PostgreSQL Global Development Group. (2024). *PostgreSQL Extended Query Protocol*. https://www.postgresql.org/docs/16/protocol-flow.html
