# Product Backlog & Sprint Plan

**Document ID:** DOC-009  
**Version:** 1.3  
**Last updated:** November 2026  
**Status:** Sprint 7 in progress

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
**Status:** 🔄 In Progress (Layers 1–6 complete; contact scheduler and ground-track trail delivered; hybrid Scenario 4 remaining)  
**Tests to date:** 310 new (591 cumulative)

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
| S7-14 | As a scheduler, I need a composite ILP objective (α×duration + β×relay_value + γ×data_age) so that scheduling decisions balance contact time, relay utility, and data freshness. | 2 | 13 | ✅ Done |
| S7-13 | As an operator, I need a relay coordinator so that satellites with no direct ground contact can route data through relay satellites using the ISL topology. | 6 | 13 | ✅ Done (24 tests) |
| S7-17 | As an operator, I need a contact scheduler runtime engine so that the static ILP schedule is turned into live AOS/LOS Kafka events (`contact.<sat_id>.aos/los/schedule`) with handover state and dark-satellite flags. | 3 | 8 | ✅ Done (17 tests) |

### Pending stories

| ID | User story | Layer | SP | Status |
|----|-----------|-------|----|--------|
| S7-15 | As an operator, I need Sprint 7 metrics integrated into the operator dashboard so that handover state, dark satellite alerts, and relay paths are visible in real time. | 7 | 8 | 🔄 In progress — ground-track trail delivered (15 tests); handover, dark and relay panels remaining |
| S7-16 | As a researcher and demonstrator, I need a hybrid Scenario 4 (18 satellites: 6 Molniya HEO in 6 planes × 1 sat, RAAN 0°/60°/…/300°, mean anomaly 60° × plane index, 63.4°, ~12 h; plus 12 circular LEO as 2 planes × 6 sats, 550 km, 53°; 7 ground stations incl. Svalbard and Fairbanks; ISL between any satellite pair with line of sight (LOS) within the range limit for its orbit-class pair — LEO–LEO 5,000 km, HEO–LEO and HEO–HEO 45,000 km) so that (1) its satellites train both Isolation Forest models, separated by orbit type (HEO_MOLNIYA from the six HEO satellites, LEO_CIRCULAR from the twelve LEO satellites, ADR-016), and (2) the composite ILP and relay-and-forward of dark satellites' data are validated through HEO relay hubs and continuous polar coverage that survives the loss of any one HEO satellite (paper P-S7-2). Replaces the former standalone Molniya Scenario 4 and the former Scenario 5 (S7-18). | 7 | 13 | 🔄 Pending — estimate revised (was 8 + 8); configuration, simulator orbit model, per-class ISL ranges and tests drafted, dashboard panels remaining |
| S7-18 | Merged into S7-16 (hybrid Scenario 4). The former Scenario 5 no longer exists; ILMOP has four scenarios. | 7 | — | ➖ Merged |

---

## Sprint 8 — MOS Completeness (planned)

**Version:** v0.8.0-beta  
**Status:** ⏳ Planned  
**Rationale:** Closes the gaps identified against the LEO MOS Architecture Study Guide so that the system deployed in Sprint 9 is complete, not one with known gaps.

| ID | User story | Priority | SP | Notes |
|----|-----------|----------|----|-------|
| S8-01 | As a developer, I need telemetry schemas expressed as Avro or Protobuf IDL and served by a schema registry so that producers and consumers share one serialisation contract. | Must | 8 | Pydantic models remain the Python-native interface |
| S8-02 | As a developer, I need schema versioning with backward/forward compatibility rules so that schema changes do not break existing consumers. | Must | 5 | |
| S8-03 | As an operator, I need structured command authoring with parameter validation so that malformed commands are rejected before approval. | Must | 5 | Command path step 1 |
| S8-04 | As an operator, I need a command approval gate (DRAFT→APPROVED→UPLINKED→CONFIRMED/FAILED) so that no command is uplinked without operator sign-off. | Must | 5 | Command path steps 2–4 |
| S8-05 | As an operator, I need exactly-once command delivery (deduplication and idempotency keys) so that a command is never executed twice. | Must | 8 | |
| S8-06 | As a tester, I need uplink simulation into the satellite simulator so that the full command loop can be verified end to end. | Should | 5 | |
| S8-07 | As a ground segment engineer, I need a CCSDS frame ingestion and decommutation layer (`services/frame_processor/`) so that raw Space Packets / TM Transfer Frames are decoded into the existing telemetry schema. | Must | 13 | Bridges clean simulated telemetry and a realistic receive chain |
| S8-08 | As a security owner, I need command authentication so that only authorised operators can author, approve, or uplink commands. | Must | 8 | Basic security scope |
| S8-09 | As a researcher, I need an ADR for the command path design (ADR-021) so that exactly-once and approval decisions are formally recorded. | Must | 3 | |

**Sprint 8 estimate:** 60 SP

---

## Sprint 9 — Cloud-Native and v1.0.0 (planned, was Sprint 8)

**Version:** v1.0.0  
**Status:** ⏳ Planned  
**Rationale:** Cloud deployment is a wrapper around a feature-complete system, done once when features are stable.

| ID | User story | Priority | SP | Notes |
|----|-----------|----------|----|-------|
| S9-01 | As an operator, I need Azure AKS Kubernetes manifests so that all ILMOP services can run in a cloud-native cluster. | Should | 13 | Deferred from Sprint 7 |
| S9-02 | As an operator, I need Azure Event Hubs (Kafka-compatible) so that streaming works without an on-premises Kafka cluster. | Should | 8 | Zero code change — Kafka wire protocol |
| S9-03 | As a data engineer, I need Azure Database for PostgreSQL Flexible Server with TimescaleDB extension so that historical telemetry persists in the cloud. | Should | 8 | |
| S9-04 | As a security owner, I need Azure Key Vault so that secrets are held outside environment variables. | Should | 5 | |
| S9-05 | As a researcher, I need MLflow artefacts migrated to Azure Blob Storage so that experiment artefacts are cloud-persisted. | Should | 5 | |
| S9-06 | As a demonstrator, I need a four-scenario progressive cloud demo (Scenarios 1→4) so that the full system is shown running on Azure. | Should | 8 | |
| S9-07 | As a researcher, I need ADR-019 (Azure platform) published so that the cloud platform decision is formally recorded. | Must | 3 | ADR-020 already published |
| S9-08 | As a release owner, I need the v1.0.0 tag and release so that the completed platform is formally released. | Must | 3 | |

**Sprint 9 estimate:** 53 SP

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
| Sprint 7 (to date) | 96 | 257 |
| Sprint 7 (remaining) | 21 | — |
| Sprint 8 (estimate) | 60 | — |
| Sprint 9 (estimate) | 53 | — |
