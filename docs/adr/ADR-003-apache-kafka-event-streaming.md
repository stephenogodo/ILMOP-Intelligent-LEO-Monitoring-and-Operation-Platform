# ADR-003 — Apache Kafka as the Event Streaming Backbone

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP must stream telemetry from up to 24 simultaneous satellites at 1 Hz each (24 records/second in real time, up to 1,440 records/second at 60× simulation speed) to multiple downstream consumers: the TimescaleDB sink, the anomaly detector, and any future consumers. The architecture must decouple producers from consumers and provide replay capability for re-running the anomaly detector on historical data.

---

## Decision

Apache Kafka running in KRaft mode (no ZooKeeper dependency) is the event streaming backbone for all ILMOP telemetry and alarm events.

---

## Rationale

Kafka was designed specifically for high-throughput, low-latency event streaming at production scale (Kreps, Narkhede, & Rao, 2011). Its topic-based pub/sub model with consumer group offsets provides exactly the decoupling and replay semantics ILMOP requires. The wildcard topic subscription pattern (`telemetry.*`) allows the sink and detector to discover new satellite topics automatically without reconfiguration — when Scenario 3 creates 24 telemetry topics, both consumers detect them without restart.

Kafka's durable log means that if the anomaly detector is restarted, it can replay records from the last committed offset, ensuring no telemetry is missed. This is operationally critical for the alarm pipeline: a detector restart during a fault injection sequence must not lose the anomalous records that follow.

KRaft mode eliminates the ZooKeeper dependency, reducing the operational footprint from three processes to one. This is appropriate for a single-node development and demonstration deployment.

The Kafka wire protocol compatibility is an important future consideration: Azure Event Hubs exposes a Kafka-compatible endpoint, meaning the Sprint 7 cloud migration requires only endpoint configuration changes, not application code changes.

---

## Consequences

**Positive:**
- Wildcard consumer pattern discovers new satellite topics automatically
- Durable log enables detector replay from any offset
- Multiple independent consumers (sink + detector) receive all records via consumer groups
- Azure Event Hubs Kafka compatibility enables zero-code Sprint 7 migration
- KRaft mode: single-container deployment, no ZooKeeper

**Negative:**
- Kafka adds infrastructure complexity compared to a simple queue
- First-start initialisation takes 30–60 seconds before the broker is ready
- Requires explicit topic retention policy to prevent unbounded disk growth

---

## Alternatives Considered

- **RabbitMQ:** Strong AMQP support but no durable log replay; consumer restart loses unprocessed messages
- **Redis Streams:** Lightweight alternative but limited partitioning and no wire-protocol cloud migration path
- **Direct database polling:** Simpler but tightly couples producer and consumer, and misses records during detector downtime

---

## References

- Kreps, J., Narkhede, N., & Rao, J. (2011). Kafka: A distributed messaging system for log processing. *Proceedings of the NetDB Workshop at VLDB 2011*. https://kafka.apache.org/papers.html
- Apache Kafka Documentation. (2024). *Apache Kafka 3.x — KRaft mode*. https://kafka.apache.org/documentation/
- Microsoft Azure. (2024). *Azure Event Hubs — Kafka protocol support*. https://docs.microsoft.com/en-us/azure/event-hubs/event-hubs-for-kafka-ecosystem-overview
