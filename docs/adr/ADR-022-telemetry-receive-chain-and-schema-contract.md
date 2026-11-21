# ADR-022 — Telemetry Contract: CCSDS Receive Chain and Avro Schema Registry

**Date:** 2026-11-21  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

Until Sprint 8 the telemetry that ILMOP handled was always a Python object, created by the simulator and passed straight to Kafka. That is convenient, but it skips two things that a real mission operations system must get right.

First, a real satellite does not send Python objects. It sends fixed-size radio frames, and the ground segment has to find the packets inside them, survive lost and damaged frames, and rebuild the telemetry. Second, once telemetry crosses a process boundary as bytes (a Kafka message), the producer and the consumer need an agreed byte layout, and that layout has to keep working when the schema changes. This has already happened once: Telemetry 2.1 added `orbit_type` (ADR-016) while records without it still existed.

Sprint 8 stories S8-07 (CCSDS frame processing), S8-01 (Avro schemas and registry) and S8-02 (schema versioning) address these two problems. This ADR records the decisions.

---

## Decision

### Part A — CCSDS receive chain (`services/frame_processor/`)

**A1 — Follow the CCSDS layering.** Telemetry is carried in CCSDS Space Packets (133.0-B-2) that are packed into TM Transfer Frames (132.0-B-3). The code is split the same way: `space_packet.py`, `tm_frame.py`, `telemetry_codec.py`, and a `processor.py` that does the ground-side work.

**A2 — Frames are verified before anything inside them is trusted.** The processor checks the length, then the CRC-16 (polynomial 0x1021, initial value 0xFFFF, the Frame Error Control Field), then the header fields, in that order. A frame that fails any check is counted and dropped. The processor never raises an error on bad input, because a receive chain that crashes on a noisy frame is worse than one that skips it.

**A3 — A lost frame discards the partial packet and resynchronises.** Each virtual channel has an 8-bit frame counter. If the counter jumps, the frames in between are counted as lost, any half-built packet is thrown away, and decoding resumes at the next packet start, which the frame's first header pointer identifies. I chose to discard rather than to guess: joining the front of one packet to the back of another would produce a telemetry record with plausible but wrong values, which is the worst possible outcome for a monitoring system. A repeated frame (same counter) is ignored.

**A4 — Counters and reassembly state are kept per spacecraft and per virtual channel.** One ground stream can carry several satellites. A loss on one must not disturb the others.

**A5 — Idle data is explicit.** A frame with no packet data is marked with the idle pointer value and counted. Unused space at the end of a frame is filled with an idle packet (APID 0x7FF), as the standard describes.

**A6 — The telemetry packet is a fixed 51-byte layout.** A 6-byte time field followed by eleven big-endian 32-bit floats and one flag byte (eclipse, contact, safe mode, anomaly, fault, HEO). The spacecraft ID in the frame, looked up in a registry, identifies which satellite the record belongs to, so the satellite name is not carried in every packet.

**A7 — The spacecraft-side builder lives in the same package.** `FrameBuilder` produces frames from telemetry, so the whole chain can be tested by round trip and by deliberately damaging the stream, without any hardware.

### Part B — Avro schema contract (`services/schema_registry/`)

**B1 — Avro, not Protobuf.** Both were allowed by the backlog. Avro suits Kafka better: the bytes carry no field names or tags, the schema is identified by a small integer, and the rules for reading data written with a different schema are part of the specification. It is also what the Confluent Schema Registry expects, which is the natural Sprint 9 destination.

**B2 — Pydantic stays the source of truth.** The Avro schema is generated from the Pydantic model, committed as an `.avsc` file so that a change is visible in review, and a test fails if the two differ. Nobody maintains two definitions by hand.

**B3 — A registry with the Confluent shape.** Subjects (`telemetry-value`, `alarms-value`), integer schema IDs shared across subjects, and per-subject version numbers. Registering an identical schema returns the existing ID, so producers can register at every start-up.

**B4 — The Confluent wire format.** One magic byte (0), a 4-byte big-endian schema ID, then the Avro payload. A consumer reads the ID, fetches the writer schema, and resolves it against its own.

**B5 — Compatibility is checked at registration.** The registry enforces BACKWARD (default), FORWARD, FULL, NONE and the transitive variants, applying Avro's schema resolution rules by field name. A rejected schema comes with a list of reasons, and the registry is left unchanged. The default is BACKWARD, which means the operational rule is: **upgrade consumers first, then producers.**

**B6 — `orbit_type` has no enum default.** Avro allows an enum to name a fallback symbol for values a reader does not know. I deliberately did not set one. If a future orbit class (for example MEO) were added, an old consumer with a fallback of LEO would read it as LEO and route it to the wrong Isolation Forest model, against ADR-016. Without a fallback the old consumer rejects the record, which is visible and safe.

**B7 — A hand-written codec for the subset I need.** Null, boolean, long, double, string, enum, timestamp-micros, optional unions and flat records. This adds no dependency and keeps the encoding rules readable. To guard against a subtle bug, a test compares the encoded bytes with `fastavro` whenever that package is installed; the check agrees byte for byte.

---

## Rationale

**Why discard on loss, not repair.** Space link protocols give frame counters precisely so that loss can be detected, not corrected. Repair needs a retransmission procedure or error-correcting code, neither of which this layer has. Reporting the loss accurately (the `frames_lost` count) lets the operator judge link quality, and nothing wrong ever reaches the anomaly detector.

**Why a fixed layout for the packet.** The frame processor receives bytes with no schema registry beside it, as a real ground station would. A fixed, documented layout with a known length is decoded with no ambiguity, and any packet of the wrong length is counted as malformed.

**Why generate, not hand-write, the schemas.** A hand-written schema beside a Python model will eventually disagree, and the disagreement shows up as unreadable messages in production. Generation plus a drift test moves that failure to the test run.

**Why the two parts are one ADR.** They are two halves of one question: how telemetry becomes bytes and back reliably. A1–A7 cover the radio side (frames to a telemetry object), B1–B7 the streaming side (a telemetry object to a Kafka message). They meet at the Telemetry model.

---

## Consequences

**Positive:**
- The receive chain is tested end to end without hardware, including lost, repeated, corrupt, idle and interleaved multi-satellite frames, and counter wrap-around.
- A schema change that would break an existing consumer is stopped at registration, with a reason, before any message is written.
- The real 2.0 → 2.1 change is registered as history and tested in both directions.

**Negative:**
- **Forward compatibility has a blind spot, recorded as a test.** A consumer that has not been upgraded silently drops `orbit_type` from a version 2.1 record, so it cannot tell a HEO record from a LEO one. Compatibility says the bytes can be read, not that the reader understands what it lost. The rule in B5 (upgrade consumers first) is the mitigation, and it is operational, not enforced by code.
- Telemetry values in the CCSDS packet are 32-bit floats, so they carry about seven significant digits. A longitude near 170° resolves only to about 1.5 m. The Avro path uses 64-bit doubles and is exact.
- The hand-written codec has to be kept correct by tests; a library would carry that burden for me. It supports only the subset listed in B7.

---

## Alternatives Considered

- **Protobuf.** Strong tooling and compact, but its compatibility rules rest on field numbers and conventions, not on a schema resolution step in the format, and it fits the Confluent registry less directly.
- **JSON with a Pydantic model on both ends.** What the project did before. Readable, but larger on the wire, has no schema identity, and has no way to state or check evolution rules.
- **The `fastavro` or `avro` library for encoding.** Reasonable and a likely future swap. I avoided a new dependency at this stage and used `fastavro` as an independent check instead.
- **Repairing lost frames, or buffering until a retransmission.** Needs a retransmission protocol that this system does not have.
- **Carrying the satellite name inside each telemetry packet.** Wastes bytes and departs from how spacecraft are identified in CCSDS frames (by spacecraft ID).

---

## Limitations and deferred work

- No channel coding (Reed–Solomon or LDPC), no attached sync marker, no operational control field, no frame secondary header, and no telecommand frames. Only TM frames on virtual channels carrying one kind of telemetry packet.
- The receive chain is not yet connected to Kafka: nothing publishes the decoded telemetry to a topic.
- The registry is in memory, has no HTTP API and no authentication, and is lost on restart. Sprint 9 would replace it with the Confluent-compatible service on Azure.
- The Command schema has no Avro form yet. Its free-form parameters map needs types the codec does not support (map or a JSON string field).
- Alarms and Telemetry are registered, but the existing Kafka producer and consumer still use JSON; moving them to the serialiser is later work.
- Schema checks run at registration. Nothing yet checks a consumer's reader schema against the registry at start-up.

---

## References

- CCSDS. (2020). *Space Packet Protocol* (CCSDS 133.0-B-2, Blue Book, Issue 2). Consultative Committee for Space Data Systems.
- CCSDS. (2015). *TM Space Data Link Protocol* (CCSDS 132.0-B-3, Blue Book, Issue 3). Consultative Committee for Space Data Systems.
- Apache Software Foundation. (2022). *Apache Avro Specification* (version 1.11.1), sections on binary encoding, logical types and schema resolution.
- Confluent. (n.d.). *Schema Registry: Schema Evolution and Compatibility; Wire Format*. Confluent documentation.
