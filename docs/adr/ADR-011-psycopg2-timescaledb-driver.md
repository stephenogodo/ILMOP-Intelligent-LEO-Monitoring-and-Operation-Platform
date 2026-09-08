# ADR-011: psycopg2 (synchronous) as the TimescaleDB driver for the sink service

**Status:** Accepted
**Sprint:** 3
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

The `services/telemetry_sink/` service must write validated telemetry
records to TimescaleDB. A database driver must be selected. Two Python
PostgreSQL drivers are in common use:

- **psycopg2** — the long-established synchronous driver
- **asyncpg** — a newer, high-performance asynchronous driver

The choice has architectural implications: a synchronous driver forces
a synchronous service design; an async driver enables concurrent I/O
within a single process. The sink service's design must be decided
alongside the driver choice.

The sink service has one responsibility: consume records from Kafka
and write them to TimescaleDB. It is a dedicated, single-purpose
consumer process.

---

## Decision

`psycopg2-binary` is the database driver for `services/telemetry_sink/
sink.py`. The sink uses a synchronous blocking consumer loop with
batched writes via `psycopg2.extras.execute_batch()`.

`asyncpg` is deferred to Sprint 4, where it is the correct choice for
the FastAPI REST layer (which is inherently async).

---

## Alternatives considered

### asyncpg
A pure-Python async PostgreSQL driver built on Python's `asyncio`.
Significantly faster than `psycopg2` for high-concurrency workloads
(benchmarks show 3–5× higher throughput when many coroutines are
writing concurrently). Used by FastAPI's recommended database
integration patterns (SQLAlchemy async, databases, encode/databases).

**Rejected for the sink (deferred to Sprint 4) because:**

The sink service is a **dedicated consumer process** with a single
blocking loop: poll Kafka → validate → buffer → write → commit → repeat.
There is no concurrency within the sink — each step must complete before
the next begins, because Kafka offset commits must be tied to successful
database writes. Introducing `asyncio` into this loop adds coroutine
management complexity (`async def run()`, `asyncio.run()`, `await`
throughout) without enabling any additional concurrency, because the
design is inherently sequential.

`asyncpg` will be used in Sprint 4 for the FastAPI layer, where its
async model is natural: multiple HTTP requests are served concurrently,
each needing a database connection from a pool. That is the workload
asyncpg is optimised for.

### SQLAlchemy (Core or ORM, async or sync)
A full SQL toolkit and ORM with support for both synchronous (`psycopg2`
backend) and asynchronous (`asyncpg` backend) operation.

**Rejected because:** SQLAlchemy's abstraction layer is appropriate
when the application needs ORM-level entity management, relationship
loading, or database-agnostic query building. The sink has one query:
a parameterised `INSERT` with `ON CONFLICT DO NOTHING`. Writing this
as raw SQL with `psycopg2.extras.execute_batch()` is simpler, faster,
and more transparent than expressing it through SQLAlchemy's query
builder. SQLAlchemy will be reconsidered for the FastAPI layer (Sprint
4) if complex query building is needed.

### databases (encode/databases)
An async query library that wraps `asyncpg`, `aiopg`, and `aiomysql`
behind a unified interface. Used in some FastAPI tutorials.

**Rejected because:** `databases` is in low-maintenance mode (infrequent
releases, limited active development). For a new project, `asyncpg`
directly or SQLAlchemy async are both better-supported choices for the
async path. For the synchronous sink, `psycopg2` is the correct choice
regardless.

---

## Rationale

**psycopg2 is the correct driver for the sink's access pattern.**

The sink's write path is:

```
buffer = []
for msg in kafka_consumer:
    record = parse_and_validate(msg)
    buffer.append(record)
    if len(buffer) >= BATCH_SIZE or timeout_elapsed:
        execute_batch(cursor, INSERT_SQL, buffer)   # single round-trip
        conn.commit()
        kafka_consumer.commit()
        buffer = []
```

`psycopg2.extras.execute_batch()` sends all records in a batch as a
single round-trip to the database using the PostgreSQL extended query
protocol. For a 50-record batch at 1 Hz telemetry cadence, this means
one database round-trip every 50 seconds — well within psycopg2's
performance envelope without any async complexity.

**The Kafka offset commit is tied to the database commit.** This is the
architectural reason the sink must be synchronous: if the database write
fails, the Kafka offset must not be committed, so the records are
reprocessed on restart. In an async design, coordinating the database
commit future with the Kafka commit callback requires careful error
propagation that is easy to get wrong. The synchronous design makes
the success/failure path explicit and testable.

**Batch parameters are tuned for 1 Hz telemetry:**
- `BATCH_SIZE = 50` — at 1 Hz, a batch commits every 50 seconds
- `BATCH_TIMEOUT_S = 5.0` — if fewer than 50 records arrive, commit
  after 5 seconds to bound data latency

These can be adjusted via `shared/config.py` in Sprint 4 when multi-
satellite operation increases the record arrival rate.

---

## Consequences

### Positive
- Synchronous consumer loop is simple to reason about, test, and debug
- Kafka offset commit is explicitly tied to successful database commit
  — no data loss on crash, no duplicate writes (ON CONFLICT DO NOTHING)
- `execute_batch()` sends all records in one round-trip — efficient
  for batched inserts without async overhead
- `psycopg2` is the most widely documented PostgreSQL driver in Python;
  debugging resources are abundant

### Negative / trade-offs
- `psycopg2-binary` packages a compiled C extension; on some minimal
  Docker base images (Alpine), the binary wheel may not be available
  and source compilation may be required — use `python:3.12-slim`
  (Debian-based) as the base image to avoid this
- The synchronous driver blocks the thread during I/O — acceptable
  for a dedicated single-purpose process, not acceptable for a
  shared web server (FastAPI)
- `execute_batch()` is efficient but not as fast as `asyncpg`'s
  COPY protocol for very high-volume bulk loads; if telemetry rate
  grows beyond 100 records/second/satellite, reconsider

### Implications for future sprints
- Sprint 4 (FastAPI): use `asyncpg` with a connection pool for the
  REST layer — the two drivers coexist without conflict
- Sprint 5 (ML training data): bulk export for model training uses
  `psycopg2` with a server-side cursor (`cursor.fetchmany()`) to
  stream large result sets without loading the full dataset into memory
- Sprint 6 (Azure): `psycopg2` connects to Azure Database for
  PostgreSQL with only a connection string change in `shared/config.py`;
  SSL mode may need to be added (`sslmode=require`) for Azure compliance
