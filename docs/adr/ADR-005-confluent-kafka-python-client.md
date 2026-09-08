# ADR-005: confluent-kafka as the Python Kafka client

**Status:** Accepted
**Sprint:** 1
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Having decided to use Apache Kafka as the event streaming backbone (ADR-003), a Python client library must be selected. The client is the
code that the producer, consumer, and sink services use to interact with the Kafka broker. Three Python Kafka clients are in common use.

The client must:
1. Support all Kafka features needed by ILMOP (producer keys, consumer groups, offset management, exactly-once semantics for the command path)
2. Perform reliably under sustained load (continuous 1 Hz telemetry per satellite)
3. Be actively maintained and production-proven
4. Be compatible with Azure Event Hubs' kafka-compatible API (Sprint 6)

---

## Decision

`confluent-kafka` (Confluent's Python client, wrapping `librdkafka`) is
the Kafka client for all ILMOP services.

---

## Alternatives considered

### kafka-python
A pure-Python Kafka client (`pip install kafka-python`). No C extension dependency; easy installation. The most commonly seen client in tutorials.

**Rejected because:** `kafka-python` has had significant maintenance gaps and unresolved issues in its GitHub repository. Its erformance, being pure Python, is lower than `confluent-kafka`'s `librdkafka` backend.
More critically, it lacks full support for newer Kafka protocol features and has known issues with KRaft mode (the ZooKeeper-free Kafka
configuration ILMOP uses from Sprint 1). Production use of `kafka-python` in high-throughput scenarios frequently surfaces batching and latency issues that require workarounds. For a platform intended to scale to
constellation-level throughput, a client with documented production limitations is not appropriate.

### aiokafka
An asyncio-native Kafka client. Designed for Python's async ecosystem and integrates naturally with FastAPI (which is also async).

**Not rejected outright — deferred:** `aiokafka` is the better choice for the FastAPI layer (Sprint 4), where async I/O is the natural model.
It is worth reconsidering for the `services/api/` consumer in Sprint 4. However, for the simulator, producer, and sink services — which are synchronous by design — `confluent-kafka`'s synchronous API is simpler and its `librdkafka` backend provides superior throughput. A mixed
approach (confluent-kafka for producers and sinks, aiokafka for the FastAPI consumer) is worth evaluating in Sprint 4.

---

## Rationale

`confluent-kafka` wraps `librdkafka`, the C library that underlies
Confluent's production Kafka deployments and is the most battle-tested Kafka client implementation across all languages. 

Key advantages:

- **Performance:** `librdkafka`'s producer batching and consumer fetch pipeline are optimised for sustained high-throughput operation — more than adequate for ILMOP's telemetry rates 

- **Feature completeness:** supports all Kafka features including
  idempotent producers, exactly-once semantics, consumer group rebalancing, and offset management — needed for the command pipeline's stronger delivery guarantees (Sprint 3)
- **Maintenance:** actively maintained by Confluent with commercial support; `librdkafka` is the reference implementation for the Kafka
  protocol
- **Azure Event Hubs compatibility:** Azure Event Hubs' Kafka-compatible endpoint is tested and documented against `confluent-kafka` — the
  Sprint 6 migration is explicitly supported

---

## Consequences

### Positive
- Production-grade performance and reliability from day one
- Full Kafka feature support including exactly-once producers for the command path (Sprint 3+)
- Azure Event Hubs compatibility confirmed for Sprint 6 migration
- Active maintenance and Confluent documentation

### Negative / trade-offs
- Requires compilation of `librdkafka` C extension at install time;
  on some platforms (particularly Alpine Linux Docker images) this requires build tools not present in the base image 
- Slightly more complex API than `kafka-python` for basic use cases
- Not asyncio-native; synchronous call model does not integrate naturally with FastAPI's async request handlers 

### Implications for future sprints
- Sprint 3 (sink service): `confluent-kafka`'s synchronous consumer loop is appropriate for the sink's dedicated consumer thread
- Sprint 4 (FastAPI): evaluate `aiokafka` for the WebSocket/SSE feed to the dashboard; `confluent-kafka` in a thread pool executor is a
  viable alternative if a single client is preferred 
  - Sprint 6 (Azure): connection string change only; `confluent-kafka` to Azure Event Hubs is a documented, supported path
