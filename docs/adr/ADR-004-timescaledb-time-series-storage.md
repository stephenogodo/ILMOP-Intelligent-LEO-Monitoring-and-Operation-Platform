# ADR-004 — TimescaleDB for Time-Series Telemetry Storage

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP stores satellite telemetry records at 1 Hz per satellite, producing up to 86,400 records per satellite per simulated day. At 24 satellites and 60× speed, a 1-hour wall-clock run generates over 5 million records. The storage layer must support efficient time-range queries (e.g., last 2 hours of telemetry for one satellite), aggregations by time bucket, and the standard SQL queries used in the ML training pipeline.

---

## Decision

TimescaleDB (PostgreSQL 16 extension) is the storage backend for all ILMOP telemetry and alarm records.

---

## Rationale

TimescaleDB extends PostgreSQL with hypertables — automatically partitioned time-series tables that provide time-range queries 10–100× faster than unpartitioned PostgreSQL (Freedman, Kirilov, & Schneider, 2019). The hypertable for the `telemetry` table is partitioned by time with a 1-hour chunk interval, so time-range queries access only the relevant chunks rather than scanning the full table.

Critically, TimescaleDB is a PostgreSQL extension rather than a separate database. This means the full SQL query language, transaction semantics, and `psycopg2` driver compatibility are preserved. The ML training pipeline uses a standard SQL `SELECT` with `WHERE orbit_type = %s AND fault_injected = FALSE` — no specialised time-series query language is required.

The automatic compression policy compresses chunks older than 2 hours at approximately 10:1 compression ratio, making multi-day telemetry storage practical on a development laptop without manual maintenance.

---

## Consequences

**Positive:**
- Hypertable partitioning provides sub-second time-range queries on millions of records
- Full SQL compatibility — training queries, API queries, and ad hoc analysis use the same language
- Automatic compression reduces storage by ~90% for cold chunks
- Docker container with the official TimescaleDB image requires no additional configuration

**Negative:**
- Requires PostgreSQL as the underlying database (adds ~500 MB to the container image)
- TimescaleDB-specific features (hypertables, compression policies) add complexity for developers unfamiliar with the extension

---

## Alternatives Considered

- **InfluxDB:** Purpose-built time-series database but uses Flux/InfluxQL rather than SQL, breaking the ML training pipeline's use of standard queries
- **Plain PostgreSQL:** Full SQL compatibility but no time-series partitioning; time-range queries degrade to O(n) at scale
- **SQLite:** Development-friendly but no concurrent write support required by the multi-service sink architecture

---

## References

- Freedman, M., Kirilov, E., & Schneider, C. (2019). TimescaleDB: An open-source time-series SQL database optimised for fast ingest and complex queries. *Proceedings of VLDB 2019*, 12(12), 2050–2063. https://doi.org/10.14778/3352063.3352129
- PostgreSQL Global Development Group. (2024). *PostgreSQL 16 Documentation*. https://www.postgresql.org/docs/16/
- Timescale Inc. (2024). *TimescaleDB Documentation*. https://docs.timescale.com/
