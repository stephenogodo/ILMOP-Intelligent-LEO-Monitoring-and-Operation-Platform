# ADR-023 — Wiring the Command and Telemetry Paths to Kafka and the API

**Date:** 2026-11-25  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

Sprint 8 built the parts of a real mission operations path: a command lifecycle with approval and authentication (ADR-021), a CCSDS receive chain and an Avro schema registry (ADR-022). They were deliberately built as pure logic with no I/O, and at the end of the sprint they were not connected to anything. Commands could only be driven from Python. Decoded frames were never published. The Kafka producer and consumers still wrote and read JSON.

Sprint 9 connects them. This ADR records the decisions I made while doing it, and the limits I accepted. It continues ADR-021 and ADR-022 and does not replace them.

---

## Decision

**D1 — Avro is introduced by a dual-read migration, not a switch.** Every consumer (the sinks, the anomaly detector, the dashboard consumer) detects the format from the first byte: the Confluent wire format starts with a zero byte, and JSON never does. Producers write one format, chosen by `KAFKA_SERIALIZATION` (`json` or `avro`). The default is `json`, and in that mode the bytes on the topic are exactly what they were before. Consumers must be upgraded before any producer is switched to `avro`.

**D2 — A message codec layer hides the format.** `services/messaging/` holds a small `MessageCodec` interface and project codecs for Telemetry and Alarm. Services call `encode_telemetry` or `decode_telemetry` and never see Avro or JSON directly.

**D3 — The schema registry is built per process from the committed schema files.** Each process registers the `.avsc` files in the same order, so each assigns the same schema IDs. This is enough while every process runs the same code version. It is not a shared registry, and a real one is Sprint 10 work.

**D4 — A small bus interface sits under the command loop.** `InMemoryBus` and the Kafka-backed `KafkaPublisher` and `KafkaSubscriber` share `publish(topic, key, value)` and `poll(timeout)`. The whole loop is tested without a broker, and the Kafka classes are thin wrappers. `KafkaPublisher.publish` waits for delivery and raises `PublishError` on failure, so a lost send is never silent.

**D5 — Two topics per satellite carry the command loop.** `commands.{sat}.uplinked` carries approved commands to the satellite, and `commands.{sat}.acks` carries its answers. The `requested` and `approved` topics that the ADD also defines are not used: the approval gate lives in the command service, not on a topic.

**D6 — The claim still comes before the send.** The ground side moves a command APPROVED → UPLINKED with a compare-and-swap, and only then publishes it. A failed publish marks the command FAILED immediately. This keeps ADR-021's at-most-once guarantee on the ground side.

**D7 — Acknowledgements are applied by a listener that cannot approve.** The `AckListener` acts as a SYSTEM actor, `ack-listener`. It confirms a command on an accepted ack and fails it on a rejected one. It counts and ignores duplicate, conflicting, unknown, mismatched and malformed acks, and never raises on a bad message.

**D8 — Out of contact means rejected, and a rejection is final.** A satellite that is out of contact rejects the command with "no contact", and the command becomes FAILED. The operator submits a new command rather than retrying the old one, so each attempt has its own audit trail.

**D9 — The command endpoints are thin and token-based.** `POST /auth/login`, `POST /auth/logout`, `POST /commands`, `GET /commands/{id}` and `POST /commands/{id}/approve|reject|uplink` carry a bearer token (RFC 6750). The routes only translate HTTP to `SecureCommandService` calls, so authentication, roles and the audit log are not duplicated. Errors map to 401, 403, 404, 409 and 422. An uplink that is refused (already sent, or the publish failed) returns 200 with `sent: false` and the reason.

**D10 — Accounts come from one setting, and there are no default credentials.** `COMMAND_ACCOUNTS_JSON` lists the accounts. If it is empty, every command endpoint answers 503. A malformed value stops the API at start-up on purpose, so a typo cannot leave the service open or half configured.

**D11 — Uplink and the acknowledgement listener are switched on together.** `COMMANDS_ENABLED=true` makes the API publish uplinks and run the listener on a background thread. Without it, the other endpoints work and uplink answers 503.

**D12 — Frame ingest is a separate service with pluggable sources.** `services/ingest/` feeds raw frames to the `FrameProcessor` and publishes each decoded record to `telemetry.{sat}` through the same codec as every other producer, so nothing downstream can tell it from the simulator. A frame source is anything that yields raw bytes. I built a simulated downlink (with optional deliberate frame loss) and a byte-stream splitter for replay. A failed publish is counted and ingest continues, because a record lost to a broker outage cannot be fetched again from a radio link.

---

## Rationale

- **Why dual-read.** A hard switch to Avro would break every consumer that was not upgraded at the same moment. With dual-read, each service can be upgraded and rolled back on its own, and the default changes nothing.
- **Why a per-process registry now.** A networked registry is real infrastructure with its own failure modes. I could prove the serialisation contract, and test it, without it. Sprint 10 replaces it with a hosted one.
- **Why the bus interface.** The command logic is the part that must be right. An in-memory bus lets me test races, outages and lost messages deterministically.
- **Why a rejected command is not retried.** A retry on the same command would blur the audit trail and weaken the at-most-once rule.
- **Why 503 instead of default accounts.** A default credential is the usual way a control path is left open. Refusing to run without configured accounts is safer than a convenient default.

---

## Consequences

**Positive:**
- The loop from operator to satellite and back now works end to end over HTTP and the message bus, and is tested without a broker.
- Telemetry can reach Kafka from either the simulator or the receive chain, in either format, with no change downstream.
- No default behaviour changed: JSON stays the default and the command endpoints are off unless accounts are configured.

**Negative:**
- A lost acknowledgement leaves the command UPLINKED indefinitely. ADR-021 already accepted this, and no ack timeout was built.
- Until every consumer is upgraded, switching a producer to `avro` is unsafe, and an un-upgraded consumer drops `orbit_type` silently. This is recorded as a test and as an operating rule.
- Replacing the registry later means schema IDs must be reconciled with those already on the topics.

---

## Alternatives Considered

- **Switch every service to Avro in one step.** Rejected: it needs all services to change and be deployed together, and gives no way back.
- **Run a real Schema Registry now.** Rejected for this sprint: it adds a service to run and secure before the contract itself was proven.
- **Per-stage topics for `requested` and `approved`.** Rejected: they would put the approval decision on a topic that anyone with broker access could write to. The approval gate is in the command service.
- **Retry a failed command in place.** Rejected: see the rationale above.
- **A bearer token in a cookie.** Rejected: it would invite cross-site request forgery for no benefit to a header-based API.

---

## Limitations and deferred work

- The command store, sessions and accounts are in memory and are lost on restart, so idempotency holds only within one process lifetime.
- There is no acknowledgement timeout. A command whose ack never arrives stays UPLINKED, and an operator must notice it.
- Commands travel as JSON: the Command schema has no Avro form, because its free-form parameters map needs a type the codec does not support.
- The registry is per process and not shared. Schema checks happen at registration and not at consumer start-up.
- The command topics (`commands.{sat}.uplinked` and `.acks`) are not created automatically in the local Docker setup, and nothing in the services creates them. Until they exist the API's listener logs an "unknown topic" warning and carries on, and it picks the topic up once it is created. `scripts/command_loop_kafka_check.py` creates its own; for the API I create them by hand (see the README).
- The Kafka classes and the full loop were run once against a real local broker at the end of the sprint: the loop check passed both cases, frame ingest published to `telemetry.SAT-001`, and a command submitted over HTTP was uplinked, answered by the satellite process and failed by the acknowledgement listener with "no contact". Nothing has been run against a hosted broker.
- Frame ingest has no real antenna source (UDP, serial or file tail), no channel coding and no telecommand frames.
- There is no endpoint to list commands, and no TLS on the API.

---

## References

- Apache Software Foundation. (2022). *Apache Avro Specification* (version 1.11.1), sections on binary encoding and schema resolution.
- Confluent. (n.d.). *Schema Registry: Schema Evolution and Compatibility; Wire Format*. Confluent documentation.
- Jones, M., & Hardt, D. (2012). *The OAuth 2.0 Authorization Framework: Bearer Token Usage* (RFC 6750). Internet Engineering Task Force.
- CCSDS. (2020). *Space Packet Protocol* (CCSDS 133.0-B-2, Blue Book, Issue 2). Consultative Committee for Space Data Systems.
- CCSDS. (2015). *TM Space Data Link Protocol* (CCSDS 132.0-B-3, Blue Book, Issue 3). Consultative Committee for Space Data Systems.
