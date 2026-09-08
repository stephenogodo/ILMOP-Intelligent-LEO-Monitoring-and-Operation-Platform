# ADR-004: TimescaleDB for time-series telemetry storage

**Status:** Accepted
**Sprint:** 1
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Processed telemetry records must be persisted for three purposes:
1. **Historical trending** — operators query "show me battery voltage for SAT-001 over the last 72 hours"
2. **ML training data** — the Sprint 5 anomaly detection model trains on weeks or months of historical telemetry
3. **API queries** — the Sprint 4 FastAPI layer serves telemetry to the Streamlit dashboard and external consumers

Telemetry is inherently time-series data: every record is timestamped,queries almost always include a time range, and the dominant access
pattern is "all records for satellite X between time A and time B." The write rate is high (one record per satellite per second at 1 Hz
cadence) and grows linearly with constellation size.

The storage solution must:
1. Handle high-frequency time-ordered writes efficiently
2. Support fast time-range queries with satellite_id filtering
3. Allow aggregation (averages, min/max over time buckets) for  dashboard trending
4. Integrate with standard SQL tooling (the developer has relational database experience)
5. Be deployable locally for development and on Azure for production
   (Sprint 6)
6. Support the ML training data access pattern (bulk export of a time-windowed dataset)

---

## Decision

TimescaleDB (PostgreSQL extension, `timescaledb/timescaledb-ha:pg16`)
is the time-series storage engine for processed telemetry. The `telemetry`table is created as a TimescaleDB hypertable partitioned on the `time`
column, with a secondary index on `(satellite_id, time DESC)`.

---

## Alternatives considered

### InfluxDB 3.0 (formerly InfluxDB OSS)
Purpose-built time-series database. High write throughput, built-in downsampling (continuous queries), and a dedicated time-series query
language (InfluxQL / Flux).

**Rejected because:** InfluxDB uses a proprietary query language (Flux)
that requires learning a non-transferable skill. Its SQL compatibility layer is incomplete. InfluxDB's data model (measurements, tags,fields)
is more complex to map a rich Pydantic schema onto than a relational table. Most importantly, InfluxDB's Azure deployment story is
container-based with no managed cloud service comparable to Azure Database for PostgreSQL — the Sprint 6 managed deployment path is
significantly harder. InfluxDB's strengths (extreme write throughput, built-in downsampling) are not needed at ILMOP's scale.

### QuestDB
An extremely high-performance time-series database with a PostgreSQL wire protocol and SQL support. Benchmarks show write rates of millions
of rows per second.

**Rejected because:** QuestDB's ecosystem maturity is lower than TimescaleDB's — fewer production deployments, less documentation, fewer
community examples for the satellite operations domain. Its SQL support, while good, is not complete PostgreSQL compatibility. No managed Azure service exists. For a research platform where reliability and ecosystem depth matter more than maximum write throughput, QuestDB's performance advantage does not outweigh its maturity deficit.

### Apache Cassandra
A wide-column distributed database designed for high-availability, high-write-throughput workloads. Used in some production telemetry
systems at scale.

**Rejected because:** Cassandra's data model (partition keys, clustering keys) requires the access patterns to be defined at schema design time.
Ad-hoc time-range queries with satellite filtering require careful primary
key design. Cassandra's operational complexity (JVM, gossip protocol,compaction) is significant overhead for a single-developer research
platform. Cassandra is appropriate at internet-scale write rates; ILMOP's
write rate (one record per second per satellite) does not justify it.

### MongoDB
A document database that can store telemetry records as JSON documents with a timestamp field and a time-series collection type (introduced in
MongoDB 5.0).

**Rejected because:** MongoDB's time-series collections are less mature than TimescaleDB's hypertables for complex aggregation queries. SQL is a more powerful and familiar query language than MongoDB's aggregation pipeline for time-range analysis. The ML training data export pattern (bulk SELECT for a time window) is trivially expressed in SQL and requires
a complex aggregation pipeline in MongoDB.

### Plain PostgreSQL (without TimescaleDB extension)
Standard PostgreSQL with a `telemetry` table indexed on `(satellite_id, timestamp)`.

**Partially rejected:** Plain PostgreSQL works correctly for ILMOP's write rates at small scale. The rejection is of *not using* the
TimescaleDB extension, not of PostgreSQL itself. TimescaleDB adds automatic time-based partitioning (hypertables), native `time_bucket()`
aggregation functions, and column compression — all on top of standard PostgreSQL. Since TimescaleDB is a PostgreSQL extension (not a separate database), the operational difference is minimal: same connection string,
same SQL, same `psycopg2`/`asyncpg` client. The extension adds capability at near-zero additional complexity.

### DuckDB
An in-process analytical database optimised for column-oriented queries. Exceptional performance for OLAP workloads (aggregations, bulk scans).

**Rejected because:** DuckDB is an embedded database — it runs in-process, not as a standalone server. It cannot serve concurrent connections from multiple services (Kafka sink, FastAPI layer, Streamlit dashboard) without
complex locking. It is an excellent tool for the ML training data export step (bulk analytical queries on a static dataset) but cannot serve as the primary operational store for a multi-service pipeline. DuckDB is worth
introducing in Sprint 5 as a fast training data preparation tool, not as the primary store.

---

## Rationale

TimescaleDB satisfies all six requirements with minimal additional
complexity over plain PostgreSQL:

1. **High-frequency writes:** hypertable automatic partitioning keeps
   individual partition sizes manageable; write performance does not degrade as data accumulates over months
2. **Time-range queries with satellite filtering:** the `(satellite_id, time DESC)` index makes the dominant dashboard query pattern
   (`SELECT * FROM telemetry WHERE satellite_id = 'X' AND time > NOW() - INTERVAL '24 hours'`) a fast index scan
3. **Aggregation:** `time_bucket('5 minutes', time)` is a native TimescaleDB function that enables the dashboard's "5-minute rolling
   average" chart with a single SQL query
4. **Standard SQL:** all queries are standard PostgreSQL SQL; `psycopg2`, `asyncpg`, and SQLAlchemy all work without modification
5. **Azure deployment:** Azure Database for PostgreSQL Flexible Server supports the TimescaleDB extension — the Sprint 6 migration is a connection string change, not a database migration
6. **ML training data export:** `SELECT * FROM telemetry WHERE time BETWEEN t_start AND t_end ORDER BY time` is a bulk sequential scan
   optimised by TimescaleDB's chunk structure; the result feeds directly into a pandas DataFrame for model training.
The Azure migration path is particularly important: Azure Database for PostgreSQL with the TimescaleDB extension is a fully managed service with automated backups, high availability, and no operational overhead — the
ideal Sprint 6 target.

---

## Consequences

### Positive
- Full PostgreSQL SQL compatibility — all standard tooling works
- `time_bucket()` and continuous aggregates enable dashboard trending without application-level aggregation logic
- Hypertable automatic partitioning handles years of telemetry without manual partition management.
- Azure Database for PostgreSQL supports TimescaleDB — Sprint 6 migration is a connection string change
- Column compression reduces storage cost for historical telemetry by 90%+ for repetitive telemetry fields

### Negative / trade-offs
- Requires the TimescaleDB extension to be installed and the  `create_hypertable()` call to run exactly once during schema initialisation — the `telemetry_sink` must handle this idempotently
- Single-node PostgreSQL has a write throughput ceiling; for very large constellations (hundreds of satellites at 10 Hz),
  a distributed time-series database may eventually be needed 
  - The TimescaleDB Docker image is larger than a plain PostgreSQL image

### Implications for future sprints
- Sprint 3: `services/telemetry_sink/schema.sql` must include `SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE)`
  and the `(satellite_id, time DESC)` index
- Sprint 4 (FastAPI): queries use standard `asyncpg` with raw SQL or SQLAlchemy; `time_bucket()` available for aggregation endpoints
- Sprint 5 (ML): training data extracted via a single SQL query into a pandas DataFrame; `time_bucket()` used to create resampled features
- Sprint 6 (Azure): connection string in `shared/config.py` points to
  Azure Database for PostgreSQL; application code unchanged
