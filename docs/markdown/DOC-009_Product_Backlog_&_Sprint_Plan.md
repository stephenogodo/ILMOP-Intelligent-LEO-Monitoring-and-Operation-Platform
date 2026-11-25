# Product Backlog & Sprint Plan

**Document ID:** DOC-009  
**Version:** 1.6  
**Last updated:** November 2026  
**Status:** Sprint 9 complete

---

## Overview

This document records the ILMOP product backlog and sprint-by-sprint delivery plan.
Each user story is tagged with its sprint, priority, estimated story points, and
current status. Story points follow a Fibonacci scale (1, 2, 3, 5, 8, 13).

---

## Sprint 1 — Dynamic Spacecraft Telemetry Simulator

**Version:** v0.2.0-alpha  
**Status:** ✅ Complete  
**Tests:** 43/43

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S1-01 | As an operator, I need an SGP4 orbit propagator so that I can compute satellite position and velocity from TLE data. | Must | 8 | ✅ Done |
| S1-02 | As an operator, I need a battery physics model that tracks charge/discharge across eclipse cycles. | Must | 5 | ✅ Done |
| S1-03 | As an operator, I need a thermal physics model that simulates on-orbit temperature swings. | Must | 5 | ✅ Done |
| S1-04 | As a developer, I need a typed telemetry Pydantic schema so that downstream consumers can deserialise consistently. | Must | 3 | ✅ Done |
| S1-05 | As an operator, I need scenario configuration YAMLs so that I can launch multi-satellite constellations without code changes. | Should | 3 | ✅ Done |

---

## Sprint 2 — Simulator Physics Upgrade

**Version:** v0.2.1-alpha  
**Status:** ✅ Complete  
**Tests:** 43/43 (cumulative)

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S2-01 | As an operator, I need a cylindrical shadow eclipse model so that battery depletion is physically accurate. | Must | 5 | ✅ Done |
| S2-02 | As an operator, I need satellite state to persist across process restarts so that simulation can resume mid-pass. | Must | 3 | ✅ Done |
| S2-03 | As a tester, I need a `--fault` injection flag so that I can trigger battery, thermal, and panel faults on demand. | Should | 3 | ✅ Done |
| S2-04 | As an operator, I need a `--speed` multiplier so that I can run demonstrations faster than real time. | Should | 2 | ✅ Done |

---

## Sprint 3 — Streaming Telemetry Platform

**Version:** v0.3.0-alpha  
**Status:** ✅ Complete  
**Tests:** 57/57 (cumulative)

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S3-01 | As an operator, I need telemetry streamed over Apache Kafka so that multiple downstream consumers can subscribe independently. | Must | 8 | ✅ Done |
| S3-02 | As a data engineer, I need telemetry persisted in TimescaleDB as a hypertable so that I can run time-range queries efficiently. | Must | 8 | ✅ Done |
| S3-03 | As a developer, I need a centralised `shared/config.py` so that all services read from environment variables consistently. | Must | 3 | ✅ Done |
| S3-04 | As an operator, I need a 24-satellite Walker delta scenario so that I can demonstrate constellation-scale monitoring. | Should | 5 | ✅ Done |

---

## Sprint 4 — Telemetry Data Platform

**Version:** v0.4.0-beta  
**Status:** ✅ Complete  
**Tests:** 75/75 (cumulative)

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S4-01 | As an operator, I need a FastAPI REST layer so that I can query telemetry and alarms programmatically. | Must | 8 | ✅ Done |
| S4-02 | As an operator, I need a Streamlit dashboard so that I can monitor satellite health in real time. | Must | 5 | ✅ Done |
| S4-03 | As a developer, I need Redis caching so that repeated API calls do not hammer TimescaleDB. | Should | 3 | ✅ Done |
| S4-04 | As an operator, I need a typed alarm schema so that severity levels are enforced at ingestion. | Must | 3 | ✅ Done |

---

## Sprint 5 — Intelligent Digital Twin

**Version:** v0.5.0-beta  
**Status:** ✅ Complete  
**Tests:** 152/152 (cumulative)

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S5-01 | As an operator, I need an Isolation Forest anomaly detector so that out-of-family telemetry is flagged automatically. | Must | 13 | ✅ Done |
| S5-02 | As an operator, I need a two-layer hybrid detector (limit-check + ML) so that both threshold violations and statistical anomalies are caught. | Must | 8 | ✅ Done |
| S5-03 | As a researcher, I need MLflow experiment tracking so that model versions and metrics are reproducible. | Should | 5 | ✅ Done |
| S5-04 | As an operator, I need alarm suppression for standing faults so that repeated alerts do not flood the dashboard. | Should | 3 | ✅ Done |
| S5-05 | As a developer, I need the training SQL to filter unhealthy data so that the model learns from nominal telemetry only. | Must | 3 | ✅ Done |

---

## Sprint 6 — Demonstration Layer

**Version:** v0.6.0-beta  
**Status:** ✅ Complete  
**Tests:** 281/281 (cumulative)

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S6-01 | As a researcher, I need an OTFS orbital profile exporter so that Doppler-delay grids are derived from real SGP4 trajectories. | Must | 5 | ✅ Done |
| S6-02 | As a researcher, I need an OTFS channel emulator so that waveform performance can be validated against theoretical FSPL and Doppler bounds. | Must | 13 | ✅ Done |
| S6-03 | As a researcher, I need an HPOP navigation truth model (J2–J6) so that position accuracy meets the 3–5 m benchmark required by the PhD thesis. | Must | 13 | ✅ Done |
| S6-04 | As a researcher, I need a navigation validation pipeline so that GDOP, RMS error, and bias are computed and compared against theoretical bounds. | Must | 8 | ✅ Done |
| S6-05 | As an operator, I need a fleet dashboard so that all constellation satellites are visible on a single screen. | Should | 5 | ✅ Done |
| S6-06 | As an operator, I need a coverage statistics page so that ground contact fractions are reported per scenario. | Should | 5 | ✅ Done |

---

## Sprint 7 — LEO Constellation Operations Suite

**Version:** v0.7.0-beta  
**Status:** ✅ Complete (Layers 1–7, contact scheduler, dashboard panels and hybrid Scenario 4 delivered)  
**Tests to date:** 393 new (674 cumulative passing, 1 skipped)

### Completed stories

| ID | User story | Layer | SP | Status |
|----|-----------|-------|----|--------|
| S7-01 | As a scheduler, I need contact window geometry so that AOS/LOS times and max elevation are computed from station and orbital parameters. | 1 | 8 | ✅ Done |
| S7-02 | As a scheduler, I need a ContactWindow dataclass and window-merge utility so that overlapping passes from the same station are consolidated. | 1 | 3 | ✅ Done |
| S7-03 | As a scheduler, I need an ILP ground station scheduler (PuLP 2.9.0) so that multi-satellite, multi-station conflicts are resolved optimally. | 2 | 13 | ✅ Done |
| S7-04 | As a scheduler, I need a greedy fallback so that the operator gets a schedule even when the ILP solver times out. | 2 | 5 | ✅ Done |
| S7-05 | As a network engineer, I need an ISL topology manager that checks line of sight (LOS) between satellite pairs so that inter-satellite links are only formed when the two satellites have line of sight (LOS). | 3 | 8 | ✅ Done |
| S7-06 | As a network engineer, I need BFS shortest-hop routing over the ISL graph so that relay paths between any two satellites can be found. | 3 | 5 | ✅ Done |
| S7-07 | As an operator, I need a ground station handover state machine (IDLE→ACQUIRING→ACTIVE→HANDING_OVER→RELEASED) so that pass transitions are tracked unambiguously. | 4 | 8 | ✅ Done |
| S7-08 | As an operator, I need a configurable overlap margin so that brief overlaps do not trigger unnecessary handovers. | 4 | 3 | ✅ Done |
| S7-09 | As an operator, I need `get_active_station()` so that I can query which ground station a satellite is linked to at any epoch. | 4 | 3 | ✅ Done |
| S7-10 | As an operator, I need `is_dark()` so that I can determine whether a satellite has no scheduled ground contact within a configurable horizon. | 5 | 5 | ✅ Done |
| S7-11 | As an operator, I need `find_dark_satellites()` so that I can scan the whole fleet for dark satellites in one call. | 5 | 3 | ✅ Done |
| S7-12 | As an operator, I need `compute_dark_intervals()` so that I can see exactly when and for how long each satellite loses ground contact. | 5 | 5 | ✅ Done |
| S7-14 | As a scheduler, I need a composite ILP objective (α×duration + β×relay_value + γ×data_age) so that scheduling decisions balance contact time, relay utility, and data freshness. | 2 | 13 | ✅ Done — conflict groups corrected in S7-15: pairwise-overlap cliques per station plus one station per satellite (`find_exclusion_groups`); chain-merged groups had scheduled only 8 of 120 windows in Scenario 4 (now 68 of 120 with the ILP). Objective corrected in S7-17: window score = contact-time share × (α + β·relay_value + γ·data_age_norm) (`duration_weighted`, default in `build_contact_schedule`) — the additive score rewarded window count; `relay_value` and `data_age_s` now come from `EnrichedPropagator` (`services/scheduler/window_metadata.py`), previously always 0 |
| S7-13 | As an operator, I need a relay coordinator so that satellites with no direct ground contact can route data through relay satellites using the ISL topology. | 6 | 13 | ✅ Done (24 tests) |
| S7-17 | As an operator, I need a contact scheduler runtime engine so that the static ILP schedule is turned into live AOS/LOS Kafka events (`contact.<sat_id>.aos/los/schedule`) with handover state and dark-satellite flags. | 3 | 8 | ✅ Done (17 tests) |

### Dashboard, scenario and merged stories (all complete)

| ID | User story | Layer | SP | Status |
|----|-----------|-------|----|--------|
| S7-15 | As an operator, I need Sprint 7 metrics integrated into the operator dashboard so that handover state, dark satellite alerts, and relay paths are visible in real time. | 7 | 8 | ✅ Done — ground-track trail (15 tests); handover, dark-satellite and relay panels on the fleet dashboard (`services/dashboard/panel_data.py`, 14 tests; ILP schedule cached per simulated hour) |
| S7-16 | As a researcher and demonstrator, I need a hybrid Scenario 4 (18 satellites: 6 Molniya HEO in 6 planes × 1 sat, RAAN 0°/60°/…/300°, mean anomaly 60° × plane index, 63.4°, ~12 h; plus 12 circular LEO as 2 planes × 6 sats, 550 km, 53°; 7 ground stations incl. Svalbard and Fairbanks; ISL between any satellite pair with line of sight (LOS) within the range limit for its orbit-class pair — LEO–LEO 5,000 km, HEO–LEO and HEO–HEO 45,000 km) so that (1) its satellites train both Isolation Forest models, separated by orbit type (HEO_MOLNIYA from the six HEO satellites, LEO_CIRCULAR from the twelve LEO satellites, ADR-016), and (2) the composite ILP and relay-and-forward of dark satellites' data are validated through HEO relay hubs and continuous polar coverage that survives the loss of any one HEO satellite (paper P-S7-2). Replaces the former standalone Molniya Scenario 4 and the former Scenario 5 (S7-18). | 7 | 13 | ✅ Done — estimate revised (was 8 + 8); configuration, simulator orbit model, per-class ISL ranges, tests and dashboard scenario selection delivered; Isolation Forest models trained per orbit type (heo_molniya v2, leo_circular v7) after the simulator clock fix; relay and delivery experiments run |
| S7-18 | Merged into S7-16 (hybrid Scenario 4). The former Scenario 5 no longer exists; ILMOP has four scenarios. | 7 | — | ➖ Merged |

---

## Sprint 8 — Command Path, CCSDS Receive Chain and Telemetry Schema Contract (complete)

**Version:** v0.8.0-beta  
**Status:** ✅ Complete (60 of 60 SP)  
**Rationale:** Closes the gaps identified against the LEO MOS Architecture Study Guide so that the system deployed in Sprint 10 is complete, not one with known gaps.

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S8-01 | As a developer, I need telemetry schemas expressed as Avro or Protobuf IDL and served by a schema registry so that producers and consumers share one serialisation contract. | Must | 8 | ✅ Done — Avro chosen; Telemetry and Alarm schemas generated from the Pydantic models (which remain the Python-native interface) and committed under `shared/schemas/avro/`; in-memory registry and Confluent wire format in `services/schema_registry/`; ADR-022 |
| S8-02 | As a developer, I need schema versioning with backward/forward compatibility rules so that schema changes do not break existing consumers. | Must | 5 | ✅ Done — BACKWARD / FORWARD / FULL / NONE and transitive modes enforced at registration; writer-to-reader resolution on decode; Telemetry 2.0 → 2.1 (`orbit_type`) registered and tested both ways (205 tests across S8-01 and S8-02) |
| S8-03 | As an operator, I need structured command authoring with parameter validation so that malformed commands are rejected before approval. | Must | 5 | ✅ Done — command catalogue and authoring validation (`services/command/`) |
| S8-04 | As an operator, I need a command approval gate (DRAFT→APPROVED→UPLINKED→CONFIRMED/FAILED) so that no command is uplinked without operator sign-off. | Must | 5 | ✅ Done — state machine with one transition table; human-only approval; REJECTED state added |
| S8-05 | As an operator, I need exactly-once command delivery (deduplication and idempotency keys) so that a command is never executed twice. | Must | 8 | ✅ Done — delivered as effectively-once (ADR-021): idempotency keys, claim-before-send, satellite-side duplicate guard |
| S8-06 | As a tester, I need uplink simulation into the satellite simulator so that the full command loop can be verified end to end. | Should | 5 | ✅ Done — command receiver in the simulator and an uplink bridge |
| S8-07 | As a ground segment engineer, I need a CCSDS frame ingestion and decommutation layer (`services/frame_processor/`) so that raw Space Packets / TM Transfer Frames are decoded into the existing telemetry schema. | Must | 13 | ✅ Done — Space Packet and TM Transfer Frame encode/decode, CRC-16, loss and duplicate handling, multi-spacecraft (85 tests); ADR-022. Channel coding, sync marker, OCF and telecommand frames not implemented |
| S8-08 | As a security owner, I need command authentication so that only authorised operators can author, approve, or uplink commands. | Must | 8 | ✅ Done — session tokens, PBKDF2 secrets, lockout, three roles, security audit log |
| S8-09 | As a researcher, I need an ADR for the command path design (ADR-021) so that exactly-once and approval decisions are formally recorded. | Must | 3 | ✅ Done — ADR-021 |

**Sprint 8 delivered:** 60 SP. Not wired in this sprint (deferred, see ADR-021 and ADR-022, and delivered in Sprint 9 except where ADR-023 says otherwise): REST endpoints and Kafka topics for commands, Kafka publication of decoded frames, a persistent registry and command store.

---

## Sprint 9 — Wiring the Command and Telemetry Paths (complete)

**Version:** v0.9.0-beta  
**Status:** ✅ Complete (24 of 24 SP)  
**Rationale:** Sprint 8 built the command path, the receive chain and the schema contract as separate parts. This sprint connects them to Kafka and the API, so that the cloud sprint wraps a working system rather than unconnected parts.

| ID | User story | Priority | SP | Status |
|----|-----------|----------|----|--------|
| S9-01 | As a developer, I need telemetry and alarms on the Kafka path to use the Avro serialiser, with JSON still the default, so that the schema contract is enforced in flight without breaking existing consumers. | Must | 5 | ✅ Done — dual-read consumers, `KAFKA_SERIALIZATION` switch for producers (ADR-023) |
| S9-02 | As an operator, I need the command loop to run over Kafka (uplinked and acknowledgement topics) so that an approved command reaches the satellite and its answer confirms or fails the command. | Must | 8 | ✅ Done — in-memory and Kafka bus, satellite command listener, acknowledgement listener, `scripts/command_loop_kafka_check.py` |
| S9-03 | As an operator, I need REST endpoints for the command path so that commands can be submitted, approved, uplinked and read over HTTP with token authentication. | Must | 5 | ✅ Done — login, submit, read, approve, reject, uplink; accounts from configuration; 47 tests |
| S9-04 | As a ground segment engineer, I need a frame ingest service so that decoded CCSDS telemetry is published to the same Kafka topics as simulator telemetry. | Must | 3 | ✅ Done — simulated downlink with optional frame loss, replay from bytes, 29 tests |
| S9-05 | As a researcher, I need ADR-023 and the renumbered plan so that the Sprint 9 decisions and their limits are formally recorded. | Must | 3 | ✅ Done — ADR-023; cloud sprint renumbered to Sprint 10 |

**Sprint 9 delivered:** 24 SP and 214 new passing tests (1110 at the end of Sprint 8, 1324 now). Still open and recorded in ADR-023: an acknowledgement timeout, a persistent command store, an Avro form for the Command schema, a real antenna source for frame ingest, and a run of the Kafka wrappers against a real broker.

---

## Sprint 10 — Cloud-Native and v1.0.0 (planned, was Sprint 9)

**Version:** v1.0.0  
**Status:** ⏳ Planned  
**Rationale:** Cloud deployment is a wrapper around a feature-complete system, done once when features are stable.

| ID | User story | Priority | SP | Notes |
|----|-----------|----------|----|-------|
| S10-01 | As an operator, I need Azure AKS Kubernetes manifests so that all ILMOP services can run in a cloud-native cluster. | Should | 13 | Deferred from Sprint 7 |
| S10-02 | As an operator, I need Azure Event Hubs (Kafka-compatible) so that streaming works without an on-premises Kafka cluster. | Should | 8 | Zero code change — Kafka wire protocol |
| S10-03 | As a data engineer, I need Azure Database for PostgreSQL Flexible Server with TimescaleDB extension so that historical telemetry persists in the cloud. | Should | 8 | |
| S10-04 | As a security owner, I need Azure Key Vault so that secrets are held outside environment variables. | Should | 5 | |
| S10-05 | As a researcher, I need MLflow artefacts migrated to Azure Blob Storage so that experiment artefacts are cloud-persisted. | Should | 5 | |
| S10-06 | As a demonstrator, I need a four-scenario progressive cloud demo (Scenarios 1→4) so that the full system is shown running on Azure. | Should | 8 | |
| S10-07 | As a researcher, I need ADR-019 (Azure platform) published so that the cloud platform decision is formally recorded. | Must | 3 | ADR-020 already published |
| S10-08 | As a release owner, I need the v1.0.0 tag and release so that the completed platform is formally released. | Must | 3 | |

**Sprint 10 estimate:** 53 SP

**Sprint order rationale:** operational features first (job-search critical, time-sensitive), MOS completeness before cloud, cloud deployment last.

---

## Story point totals

| Sprint | Points delivered | Cumulative |
|--------|-----------------|------------|
| Sprint 1 | 24 | 24 |
| Sprint 2 | 13 | 37 |
| Sprint 3 | 24 | 61 |
| Sprint 4 | 19 | 80 |
| Sprint 5 | 32 | 112 |
| Sprint 6 | 49 | 161 |
| Sprint 7 | 109 | 270 |
| Sprint 8 | 60 | 330 |
| Sprint 9 | 24 | 354 |
| Sprint 10 (estimate) | 53 | — |
