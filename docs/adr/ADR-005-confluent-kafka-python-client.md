# ADR-005 — confluent-kafka Python Client

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

Multiple ILMOP services require Kafka producer and consumer functionality from Python: the simulator (producer), the telemetry sink (consumer), the anomaly detector (consumer), the alarm sink (consumer), and the detector's alarm publisher (producer). The Python Kafka client must provide the throughput, offset management, and consumer group semantics required by ILMOP's event-driven architecture.

---

## Decision

`confluent-kafka-python` (wrapping the `librdkafka` C library) is the Kafka client for all ILMOP services.

---

## Rationale

`confluent-kafka-python` wraps `librdkafka`, the de facto standard high-performance Kafka client library (Confluent, 2024). At the data rates produced under Scenario 3 high-speed operation (~1,440 records/second), the `kafka-python` pure-Python alternative shows significant performance degradation due to GIL contention and Python-level serialisation overhead. `librdkafka` handles these operations in a separate C thread, achieving throughput that is typically an order of magnitude higher than pure-Python alternatives.

The `confluent-kafka` API provides manual commit semantics via `consumer.commit()`, which ILMOP uses to commit offsets only after successful TimescaleDB writes. This prevents data loss if the sink crashes between consuming a record and persisting it.

The library also provides `auto.offset.reset=earliest` configuration, which enables the detector to replay all available records from the beginning of a topic when restarted — essential for re-scoring historical telemetry under a retrained model.

---

## Consequences

**Positive:**
- C-level throughput via librdkafka handles Scenario 3 high-speed data rates
- Manual offset commit prevents data loss on sink restart
- `auto.offset.reset=earliest` enables historical replay
- Confluent maintains the library with active support and regular updates

**Negative:**
- Requires C extension compilation; `pip install confluent-kafka` installs a pre-built wheel on most platforms but may require build tools on some environments
- API differs from `kafka-python`; not interchangeable without code changes

---

## Alternatives Considered

- **kafka-python:** Pure-Python, simpler installation, but insufficient throughput for high-speed Scenario 3
- **aiokafka:** Async-first, but requires restructuring all services around asyncio
- **faust:** Stream processing library built on aiokafka; appropriate for stateful stream processing but adds significant complexity for ILMOP's simple consume-score-publish pattern

---

## References

- Confluent Inc. (2024). *confluent-kafka-python — Python client for Apache Kafka*. https://github.com/confluentinc/confluent-kafka-python
- Confluent Inc. (2024). *librdkafka — The Apache Kafka C/C++ library*. https://github.com/confluentinc/librdkafka
