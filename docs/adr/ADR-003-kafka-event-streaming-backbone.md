# ADR-003: Apache Kafka as the event streaming backbone

**Status:** Accepted
**Sprint:** 1
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

ILMOP's telemetry pipeline must deliver a stream of `Telemetry` records from the satellite simulator to multiple independent consumers: a
TimescaleDB sink (persistence), an anomaly detection service (AI/ML),a FastAPI layer (REST queries), and a Streamlit dashboard (real-time
display). These consumers have different processing rates, different failure modes, and different scaling requirements.

A naive point-to-point design — where the simulator writes directly to TimescaleDB and also pushes to an anomaly detection queue and also updates the dashboard — tightly couples every component. Adding a new
consumer (e.g., a payload telemetry analyser for the PhD integration) requires modifying the simulator. A component that falls behind causes
backpressure on all others. There is no replay capability if the anomaly detection model needs to reprocess historical data.

The event streaming layer must:
1. Decouple producers from consumers completely
2. Deliver records to multiple consumers independently (fan-out) 
3. Guarantee per-satellite ordering (the battery at t=100 must be processed before the battery at t=101 for the same satellite)
4. Provide a durable, replayable log (if decommutation logic changes, reprocess from the raw record)
5. Scale horizontally as the constellation grows
6. Be compatible with the planned Azure deployment target (Sprint 6)

---

## Decision

Apache Kafka (KRaft mode, single node for development; multi-broker for Sprint 6 Azure deployment) is the event streaming backbone. All
inter-service telemetry flow passes through Kafka topics. Topic naming
convention: `telemetry.{satellite_id}`, `alarms.{satellite_id}`,
`commands.{satellite_id}.{stage}`.

---

## Alternatives considered

### Direct database writes (no messaging layer)
The simulator writes directly to TimescaleDB. Consumers query the database for new records using polling.

**Rejected because:** polling introduces latency proportional to the poll interval. Multiple simultaneous pollers create read load on the
database that competes with the write path. There is no fan-out without duplicating writes or adding a notification mechanism. Most critically, there is no replay — if the anomaly detection model needs to reprocess
the last six months of telemetry after retraining, there is no source to replay from. The database becomes both the operational store and the event log, which are architecturally incompatible roles.

### Redis Streams
An in-memory event streaming solution built into Redis. Lower operational
overhead than Kafka; supports consumer groups, acknowledgement, and
replay within the retention window.

**Rejected because:** Redis is in-memory by default; durability requires explicit AOF or RDB persistence configuration with non-trivial performance trade-offs. Retention windows are memory-bounded, making long-term replay
impractical for a telemetry archive. Redis Streams lack Kafka's partition
model — there is no native mechanism for per-satellite ordering at constellation scale. Redis is retained in the architecture as a caching layer (Sprint 4) for the "latest telemetry per satellite" read pattern, which is its appropriate role.

### RabbitMQ
A message broker with mature support for complex routing topologies (exchanges, routing keys, dead-letter queues).

**Rejected because:** RabbitMQ is a message queue, not an event log.Once a consumer acknowledges a message, it is deleted. There is no replay capability. Fan-out (multiple consumers receiving the same record)
requires explicit exchange configuration per consumer — adding a new consumer requires broker reconfiguration. For a pipeline where replay
and multiple-consumer fan-out are first-class requirements, a log-based system is architecturally superior.

### MQTT
The standard protocol for IoT and satellite telemetry in constrained environments. Used in many legacy ground systems for telemetry distribution.

**Rejected because:** MQTT brokers (Mosquitto, HiveMQ) are message brokers, not durable event logs. Replay is not supported. Consumer groups
and offset management do not exist. MQTT is appropriate for satellite-to-ground RF links and constrained edge devices; it is not appropriate as the internal bus of a ground software platform.

### AWS Kinesis / Azure Event Hubs (managed cloud-native)
Fully managed event streaming services compatible with the Kafka API (Event Hubs) or similar (Kinesis).

**Not rejected — deferred:** Azure Event Hubs exposes a Kafka-compatible API. The decision to use Kafka in development is explicitly made with
Sprint 6 migration in mind: the same `confluent-kafka` producer and consumer code will connect to Azure Event Hubs in production with only a change to the bootstrap server connection string. Event Hubs is the Sprint 6 target; local Kafka is the Sprint 1–5 development environment.

---

## Rationale

Kafka's log-based architecture directly addresses every requirement:

1. **Decoupling:** producers write to a topic; consumers subscribe independently. The simulator has no knowledge of how many consumers exist or what they do.

2. **Fan-out:** multiple consumer groups (TimescaleDB sink, anomaly detection, dashboard WebSocket feed) each receive every record
   independently, at their own pace.

3. **Per-satellite ordering:** partitioning by `satellite_id` as the
   message key guarantees that all records for a given satellite land in the same partition and are processed in order by any consumer
   of that partition.

4. **Durable replay:** Kafka's retention policy (configurable; default 7 days, can extend to months) means the anomaly detection model
   can reprocess the entire training window by resetting its consumer offset to the beginning of the topic.

5. **Horizontal scalability:**
 adding a satellite adds a partition; adding a consumer adds a consumer group member. Neither requires changes to any existing service.

6. **Azure migration path:**
 Azure Event Hubs' Kafka-compatible API means the Sprint 6 cloud deployment changes only the bootstrap server configuration, not the application code. 
 KRaft mode (Kafka without ZooKeeper) is used from Sprint 1, eliminating the operational complexity of a separate ZooKeeper cluster while retaining full Kafka semantics.

---

## Consequences

### Positive
- Complete decoupling of all pipeline components — each service is independently deployable, scalable, and replaceable 
- Replay capability enables reprocessing of historical telemetry when the anomaly detection model is retrained (Sprint 5)
- Per-satellite partitioning guarantees ordering without any application-level sequencing logic
- Fan-out to unlimited consumers without producer modification
- Azure Event Hubs migration (Sprint 6) requires zero application code changes

### Negative / trade-offs
- Kafka adds operational complexity: a broker process must be running
  before any other service can start; health checks and startup ordering in Docker Compose require careful configuration - Single-broker development setup has no fault tolerance — a broker  crash loses in-flight messages; acceptable for development, not for
  Sprint 6 production 
  - Message ordering is guaranteed only within a partition; records for different satellites may arrive at a multi-satellite consumer out of
  wall-clock order — consumers must handle this

### Implications for future sprints
- Sprint 3: `services/telemetry_sink/` consumes from Kafka; topic names must follow the `telemetry.{satellite_id}` convention; `satellite_id`  must be the message key 
  - Sprint 5: the anomaly detection service resets its consumer offset to replay training data; Kafka retention policy must be set long enough  to cover the training window
- Sprint 6: bootstrap server in `shared/config.py` switches from  `localhost:9092` to the Azure Event Hubs endpoint; all other code  is unchanged
- PhD integration: payload telemetry (OFDM SNR, Doppler, ranging residuals) flows on a parallel `payload.{satellite_id}` topic, processed
  by a dedicated payload analytics consumer without touching the housekeeping telemetry pipeline
