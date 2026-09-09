# DOC-003 — Concept of Operations (ConOps)
## ILMOP — Intelligent LEO Monitoring and Operation Platform

| Attribute | Detail |
|-----------|--------|
| **Document ID** | DOC-003 |
| **Title** | Concept of Operations (ConOps) |
| **Version** | 1.0 |
| **Status** | Active |
| **Date** | 2026-09-08 |
| **Author** | Stephen Ogodo |
| **Related documents** | DOC-007 ADD, ILMOP_Project_Tracker.md, ADR-001 to ADR-013 |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Overview](#2-system-overview)
3. [Operational Context and Problem Statement](#3-operational-context-and-problem-statement)
4. [Operational Stakeholders and Roles](#4-operational-stakeholders-and-roles)
5. [Operational Philosophy](#5-operational-philosophy)
6. [Constellation Scenarios](#6-constellation-scenarios)
7. [Operational Modes](#7-operational-modes)
8. [Normal Operations](#8-normal-operations)
9. [Contact Window Operations](#9-contact-window-operations)
10. [Anomaly Detection and Response](#10-anomaly-detection-and-response)
11. [PhD Research Operations](#11-phd-research-operations)
12. [Operational Constraints and Limitations](#12-operational-constraints-and-limitations)
13. [Interfaces](#13-interfaces)
14. [Assumptions and Dependencies](#14-assumptions-and-dependencies)
15. [Future Operational Capabilities](#15-future-operational-capabilities)

---

## 1. Introduction

### 1.1 Purpose

This Concept of Operations document describes how the Intelligent LEO
Monitoring and Operation Platform (ILMOP) is operated — by whom, under
what conditions, in what modes, and according to what procedures. It is
concerned with operational behaviour, not software implementation. Where
the Architecture Design Document (DOC-007) describes what the system is
built from, this ConOps describes what operators and researchers do with it.

The primary audiences are: satellite operators who will use ILMOP to
monitor and command a LEO constellation; researchers who will use ILMOP
as the ground segment for PhD waveform demonstrations; and reviewers
(examiners, interviewers, collaborators) who need to understand what
the platform does in operational terms before examining the technical
implementation.

### 1.2 Scope

This ConOps covers the full operational scope of ILMOP across all seven
planned sprints, describing current capabilities (Sprints 1–4) and planned
operational modes (Sprints 5–7) with clear distinctions between them. It
covers four constellation scenarios of increasing complexity, five
operational modes, and three PhD research demonstration operations. It
does not cover software architecture, implementation details, or
infrastructure configuration — those are covered in DOC-007 (ADD) and
the ADR register.

### 1.3 Definitions and abbreviations

| Term | Definition |
|------|-----------|
| **AOS** | Acquisition of Signal — the moment a ground antenna establishes a link with a satellite at the start of a contact window |
| **LOS** | Loss of Signal — the moment a satellite passes below the ground antenna's minimum elevation angle, ending a contact window |
| **Contact window** | A period during which a satellite is above the minimum elevation angle of a ground station and two-way communication is possible |
| **Eclipse** | A period during which the satellite is in Earth's shadow and receives no solar power |
| **Pass** | Synonym for contact window — one complete AOS-to-LOS period |
| **RAAN** | Right Ascension of the Ascending Node — the orbital element that defines where an orbital plane crosses the equator going north |
| **SGP4** | Simplified General Perturbations 4 — the industry-standard orbit propagation algorithm used by ILMOP for operational position computation |
| **HPOP** | High Precision Orbit Propagation — numerical integration using a full force model; used as the navigation truth reference for the PhD waveform validation pipeline |
| **ISAC** | Integrated Sensing and Communication — the waveform design paradigm of the PhD research: one OFDM waveform serving communication, navigation, and remote sensing |
| **TLE** | Two-Line Element set — a compact standard format for describing a satellite's orbit at a specific epoch, used as input to SGP4 |
| **Molniya** | A highly elliptical orbit (HEO) with 63.4° inclination and eccentricity ~0.74, providing persistent polar coverage via extended apogee dwell |
| **Digital twin** | A virtual model of the physical satellite that mirrors its state in real time, forecasts future states, and supports autonomous response within defined boundaries |
| **ILP** | Integer Linear Programming — the mathematical optimisation technique used by the Sprint 7 ground station scheduler to assign contact windows |
| **GDOP** | Geometry Dilution of Precision — a measure of how satellite geometry affects navigation accuracy; lower GDOP means better accuracy |

---

## 2. System Overview

### 2.1 What ILMOP is

ILMOP is a full-stack, AI-assisted Mission Operations System for LEO
satellite constellations. It performs the complete operational chain of
modern commercial satellite operations: physics-accurate spacecraft
simulation, real-time event streaming via Apache Kafka, time-series
persistence in TimescaleDB, REST API delivery via FastAPI, real-time
operator display via Streamlit, and anomaly detection via machine
learning.

In operational terms, ILMOP is the ground-based software system through
which a human operator monitors the health of one or more satellites,
detects anomalies before they become failures, plans and uplinks commands,
and retrieves downlinked payload data. In research terms, it is the
operational ground segment through which the PhD OFDM waveform
demonstrations are conducted, validated, and documented.

### 2.2 The digital twin concept

ILMOP implements the spacecraft digital twin concept at four levels of
maturity across its seven sprints:

- **Digital shadow (Sprints 1–2):** telemetry flows one way — from the
  satellite simulator to storage. The virtual model mirrors the physical
  satellite's state but does not influence it.

- **Digital twin (Sprints 3–4):** bidirectional data flow. Telemetry
  flows from satellite to ground; commands flow from ground to satellite.
  The platform's API and dashboard give operators a gateway for action,
  not just observation.

- **Predictive twin (Sprint 5):** the AI/ML layer forecasts future
  satellite states. The anomaly detection service identifies degradation
  trends before threshold alarms fire. The platform proposes responses
  rather than just recording events.

- **Autonomous twin (Sprints 6–7):** the ground station scheduler
  automates contact window planning. The system acts within defined
  autonomous boundaries — scheduling contacts, deferring non-critical
  payload operations — and escalates decisions outside those boundaries
  to the operator.

The transition from shadow to twin to predictive to autonomous is not
merely a feature progression. It represents a change in the operational
relationship between the human operator and the platform: from observer
to collaborator to supervisor.

### 2.3 Operational environment

ILMOP operates in two environments depending on the development phase.
In Sprints 1–5, all services run locally on a Windows development machine
with Docker Compose managing infrastructure (Kafka, TimescaleDB, Redis).
In Sprint 7, the platform migrates to Azure AKS with managed cloud
services, retaining the same application code through environment variable
configuration.

The satellite simulator substitutes for a real spacecraft. In a production
deployment with real satellites, the simulator would be replaced by a
telemetry ingestion service receiving CCSDS frames from a ground antenna
via the Space Link Extension (SLE) protocol. No other component of the
architecture requires modification for this transition — this is a
deliberate consequence of the event-driven, schema-first design.

---

## 3. Operational Context and Problem Statement

### 3.1 The LEO constellation operations challenge

Legacy satellite operations were designed for geosynchronous (GEO)
satellites: one or a small number of spacecraft, continuously visible
from a ground station, with relatively stable orbits and long contact
windows. A small team of operators could manage a GEO fleet manually —
monitoring telemetry, planning commands, and responding to anomalies
on a human timescale.

Modern LEO constellations invalidate all of these assumptions. A
24-satellite constellation at 550 km altitude generates
24 × 86,400 = 2,073,600 telemetry records per day at 1 Hz cadence.
Each satellite is visible from any single ground station for only
5–10 minutes per 95-minute orbit. Contact windows arrive and depart
faster than a human team can plan manually. Anomalies must be detected
in the telemetry stream, not reported by operators who noticed something
unusual.

The consequence is that modern LEO mission operations require software
that is event-driven (telemetry is a continuous stream, not a periodic
report), AI-assisted (anomaly detection must be automated), and scalable
(the same architecture must handle 1 satellite and 24 without redesign).
ILMOP is built around these requirements from its first sprint.

### 3.2 Why the physics of the simulator matter operationally

ILMOP's satellite simulator does not use random-walk models for battery
and temperature. It uses eclipse-aware physical models: the battery
discharges when the satellite is in Earth's shadow (no solar power),
charges in sunlight, and the solar panel output is exactly zero during
eclipse. The temperature follows a first-order thermal lag toward
separate eclipse and sunlight equilibrium targets.

This matters operationally because the anomaly detection model (Sprint 5)
trains on this simulator data. A model trained on random-walk telemetry
learns no pattern — there is nothing to learn. A model trained on
eclipse-correlated telemetry learns that a battery declining during
sunlight passes is anomalous, that a temperature that fails to recover
from eclipse is anomalous, that a satellite whose solar panel output is
non-zero in eclipse is anomalous. The physical correlation is the
operational signal that makes AI-assisted anomaly detection possible.

---

## 4. Operational Stakeholders and Roles

ILMOP defines four operational roles. In the current single-developer
research context, one person fills all four. In a team or commercial
deployment, these roles would be assigned to different personnel.

| Role | Primary responsibilities | System access |
|------|------------------------|---------------|
| **Satellite Operator** | Monitor live telemetry; acknowledge and respond to alarms; review and approve proposed commands; manage contact windows; escalate unresolved anomalies | Streamlit dashboard, command approval interface |
| **System Administrator** | Manage infrastructure (Kafka, TimescaleDB, Redis, Docker); configure ground station parameters; manage user access; monitor pipeline health and consumer lag | Docker Compose, infrastructure configs, monitoring |
| **Data Analyst / ML Engineer** | Generate training datasets; train and evaluate anomaly detection models; track experiments in MLflow; promote models to production; analyse telemetry trends | TimescaleDB direct query, MLflow UI, FastAPI |
| **PhD Researcher** | Configure constellation scenarios for waveform demonstrations; access navigation truth reference (HPOP); validate OFDM ranging residuals against orbital truth; generate coverage statistics for remote sensing demonstrations | FastAPI, NavigationTruthModel, ConstellationManager, coverage analytics |

One cross-cutting principle governs all roles: **AI models propose,
humans dispose.** No automated system in ILMOP sends commands, changes
satellite configuration, or modifies schedules without human or tightly
scoped automated policy approval.

---

## 5. Operational Philosophy

### 5.1 Human-in-the-loop by default

ILMOP's operational philosophy places human judgement at every decision
boundary that affects satellite safety or mission success. The anomaly
detection service flags and recommends — it does not command. The ground
station scheduler proposes a contact plan — operators review and approve
it. Commands move through an explicit approval chain
(requested → approved → uplinked) with a human or tightly scoped
automated policy at the approval step.

This philosophy relaxes progressively as the platform matures and as
operator confidence in specific automated behaviours grows. In Sprint 7,
certain low-risk autonomous actions — rescheduling a non-critical contact
window, deferring a payload operation that conflicts with a
higher-priority satellite — may be authorised without per-instance human
approval. High-consequence decisions (safe-mode commands, orbit
manoeuvres, payload power cycling) always require explicit operator
approval regardless of automation level.

### 5.2 Event-driven, asynchronous operations

ILMOP does not operate on a polling model. Services do not periodically
query each other for updates. Every telemetry record, every alarm, every
command state transition is an event published to a Kafka topic and
consumed independently by every service that needs it. This means:

- Adding a new consumer (a payload analytics service, a PhD navigation
  validator, a third-party monitoring integration) requires no change to
  any existing service — it simply subscribes to the relevant topic.
- A slow consumer does not block or slow other consumers. Each consumer
  group processes events at its own rate.
- Replay is always available. If the anomaly detection model is retrained,
  it can reprocess the last week of telemetry from the beginning of the
  Kafka topic without any special data pipeline work.

### 5.3 Orbit-type separation

ILMOP maintains a strict operational and analytical separation between
LEO circular orbit operations (Scenarios 1–3) and Molniya HEO operations
(Scenario 4). This separation is physically motivated, not administrative.

LEO circular satellites and Molniya HEO satellites have incompatible
telemetry signatures. A Molniya satellite at apogee spends up to 8 hours
in continuous sunlight at 39,750 km altitude — its battery stays fully
charged, its temperature is stable, its contact window with a
high-latitude ground station lasts hours rather than minutes. Training
one anomaly detection model on both data types produces a confused
baseline that fits neither orbit correctly. Each orbit type has its own
anomaly detection model, its own MLflow experiment, and its own
TimescaleDB query filter. The `orbit_type` field in the Telemetry
schema (v2.0) enforces this separation at the data layer.

---

## 6. Constellation Scenarios

ILMOP supports four operational scenarios of increasing complexity, each
defined by a YAML configuration file in `config/constellations/` and
launched by the demo runner:

```bash
python run_demo.py --scenario N --speed M
```

The `--speed` parameter controls the ratio of simulated time to
wall-clock time. At `--speed 60`, one real second produces 60 seconds
of simulated telemetry — enabling one week of training data to be
generated in approximately 2.8 hours. All four scenarios support
this parameter.

### 6.1 Scenario 1 — Single satellite

| Parameter | Value |
|-----------|-------|
| Satellites | 1 |
| Orbit type | LEO circular, 550 km altitude, 51.6° inclination |
| Orbital period | ~95.5 minutes |
| Eclipse fraction | ~33% per orbit |
| Contact frequency | One pass per ~95 minutes per ground station |
| Contact duration | 5–10 minutes per pass |
| ML model | LEO_CIRCULAR baseline — trained on Scenario 1 data as initial model |
| Primary purpose | Pipeline validation; anomaly detection baseline training; communication and navigation demonstration baseline |

Scenario 1 is the operational baseline from which all subsequent scenarios are extensions. It establishes the telemetry pattern — regular eclipse cycling, battery charge and discharge with the orbital period, thermal oscillation — that the anomaly detection model learns as normal. Any operational scenario involving a single satellite, whether for initial platform testing, PhD communication demonstration, or baseline model training, uses Scenario 1.

### 6.2 Scenario 2 — Single-plane LEO constellation (6 satellites)

| Parameter | Value |
|-----------|-------|
| Satellites | 6 (SAT-A1 through SAT-A6) |
| Orbit type | LEO circular, 550 km, 53° inclination |
| Plane configuration | 1 orbital plane, RAAN = 45°, satellites spaced 60° in mean anomaly |
| Contact frequency | One pass per ~16 minutes per ground station (6× improvement over Scenario 1) |
| Simultaneous visibility | Up to 2 satellites visible from a mid-latitude station at any moment |
| ML model | LEO_CIRCULAR model extended with 6-satellite training data |
| Primary purpose | Navigation demonstration; contact frequency improvement; multi-satellite coordination |

With six satellites spaced 60° apart in a single orbital plane, the 37° ground visibility arc at 550 km altitude means 1–2 satellites are visible per pass window. The navigation demonstration uses Doppler shift and ranging measurements accumulated across sequential satellite passes to demonstrate the OFDM waveform's navigation capability — consistent with the LEO Doppler navigation heritage of the TRANSIT system but using a modern integrated OFDM signal design.

The navigation validation pipeline compares the OFDM position fix against the HPOP NavigationTruthModel (1–10 metre accuracy) rather than against SGP4 (100–500 metre accuracy). Claiming sub-100-metre navigation accuracy against a 300-metre truth reference is not scientifically
defensible. The HPOP model is invoked only for navigation validation, not for routine operational orbit computation.

### 6.3 Scenario 3 — Multi-plane LEO constellation (24 satellites, 4 × 6)

| Parameter | Value |
|-----------|-------|
| Satellites | 24 (SAT-A1 to SAT-D6) |
| Orbit type | LEO circular, 550 km, 53° inclination |
| Plane configuration | 4 orbital planes at RAAN 0°, 90°, 180°, 270°; 6 satellites per plane, 60° spacing |
| Contact frequency | Multiple passes per hour; near-continuous coverage with a 5-station network |
| Antenna conflicts | Multiple satellites simultaneously visible — scheduling conflicts requiring ILP resolution (Sprint 7) |
| ML model | LEO_CIRCULAR production model trained on full 24-satellite dataset |
| Primary purpose | Operational scale demonstration; ground station scheduling; anomaly detection at constellation scale; remote sensing mid-latitude coverage |

Scenario 3 is the primary operational scale demonstration. The 4 × 6
constellation with 90° RAAN separation produces near-global coverage
between ±53° latitude. At this scale, the ground station scheduler
(Sprint 7) faces a genuine combinatorial optimisation problem: multiple
satellites from different planes are simultaneously visible from the same
antenna, but one antenna can serve only one satellite at a time. The ILP
scheduler resolves conflicts by maximising weighted contact time subject
to the one-satellite-per-antenna constraint, publishing the resulting
schedule as a Kafka event stream.

This scenario also provides the richest training dataset for the anomaly
detection model. With 24 satellites generating telemetry simultaneously,
the model learns the distribution of normal behaviour across a full
constellation — including the inter-plane variation in eclipse timing,
contact duration, and thermal profiles that results from the different
RAANs.

### 6.4 Scenario 4 — Molniya HEO polar constellation (standalone)

| Parameter | Value |
|-----------|-------|
| Satellites | 6 (SAT-M1 through SAT-M6) |
| Orbit type | HEO Molniya — inclination 63.4°, eccentricity 0.74, argument of perigee 270° |
| Apogee altitude | 39,750 km (over northern hemisphere) |
| Perigee altitude | 500 km |
| Orbital period | ~12 hours (2 orbits per day) |
| Apogee dwell | ~8 of 12 hours near apogee (satellite moves slowly) |
| Contact duration | Up to 8 continuous hours from Svalbard (78°N) or Fairbanks (65°N) |
| Eclipse fraction | ~5–15% (much lower than LEO — satellite mostly in sunlight at high altitude) |
| Primary ground stations | Svalbard (78.2°N, 15.4°E) and Fairbanks (64.8°N, 147.7°W) |
| ML model | HEO_MOLNIYA — separate model, never trained on LEO data |
| Primary purpose | Polar coverage demonstration; long-dwell remote sensing; antenna handover; standalone HEO operations |

Scenario 4 is operated as a completely independent mission. It never
runs concurrently with Scenarios 1–3 in the same ML training context,
and its telemetry is stored with `orbit_type = 'HEO_MOLNIYA'` to
prevent cross-contamination of training data.

The 63.4° inclination is not arbitrary — it is the critical angle at
which the J2 Earth oblateness perturbation produces zero net precession
of the argument of perigee, keeping the apogee permanently fixed over
the northern hemisphere. Any other inclination causes the apogee to
drift away from the north over months.

The operational advantage of long-dwell contact is demonstrated by the
Scenario 4 dashboard: a Molniya satellite at apogee moves slowly enough
that an operator can plan an entire day's commanding within a single
8-hour contact window. Antenna handover — transferring a contact from
Svalbard to Fairbanks as one satellite descends toward perigee — is
demonstrated with the 6-satellite spacing (60° mean anomaly separation)
that ensures a second satellite is always rising to apogee as the first
descends.

---

## 7. Operational Modes

ILMOP operates in five distinct modes. Transitions between modes are
triggered by specific conditions. The current mode is always visible
in the operator dashboard status header.

| Mode | Trigger | Key characteristics |
|------|---------|---------------------|
| **Normal operations** | Default — no anomalies detected, all services healthy | Continuous telemetry monitoring; contact windows as scheduled; anomaly detection active; dashboard auto-refreshes |
| **Contact window mode** | AOS detected (satellite above minimum elevation angle of a scheduled ground station) | Telemetry downlink active; command uplink available; CPU utilisation elevated; link rates non-zero; operator notified |
| **Anomaly response mode** | Anomaly detection service flags a WARNING or CRITICAL severity alarm | Alarm panel activated; operator notification sent; anomaly record written to database; response procedure initiated |
| **Safe mode** | Battery SoC below critical threshold; thermal exceedance; on-board fault detection triggers | Non-essential payload operations suspended; satellite in minimum-power configuration; all contact windows prioritised for recovery commanding |
| **Training / demonstration mode** | Explicit operator command (`--speed > 1` or scenario selection) | Simulator running at accelerated time; data flowing to TimescaleDB for training dataset accumulation or PhD demonstration; normal anomaly detection suspended or monitoring-only |

---

## 8. Normal Operations

### 8.1 Continuous monitoring

In normal operations, the following activities occur continuously without
operator intervention:

- The satellite simulator generates one `Telemetry` record per second per
  satellite and publishes it to `telemetry.{satellite_id}` in Kafka.
- The TimescaleDB sink consumes each record, validates it against the
  Pydantic Telemetry schema, and writes it in batches of 50 records or
  every 5 seconds.
- The anomaly detection service scores each incoming record and publishes
  an `Alarm` record to `alarms.{satellite_id}` if the Isolation Forest
  score exceeds the configured threshold.
- The Redis cache stores the latest telemetry record for each satellite
  with a 5-second TTL, enabling the dashboard to display current status
  at sub-second latency.
- The Streamlit dashboard auto-refreshes at the configured interval
  (default 2 seconds), calling `GET /telemetry/{satellite_id}/latest`
  for live status and `GET /telemetry/{satellite_id}/summary` for charts.

### 8.2 Operator responsibilities in normal mode

In normal mode, operator responsibilities are primarily supervisory:

- Review the dashboard alarm panel at least once per contact window cycle
  (~95 minutes for Scenario 1) to confirm no unacknowledged alarms.
- Review the trending charts daily for gradual drift in key health
  indicators: battery SoC trend over multiple orbits, thermal profile
  variation, CPU utilisation baseline.
- Review the upcoming contact schedule (Sprint 7) to confirm no
  scheduling conflicts require manual resolution.
- Acknowledge and close resolved alarms.

### 8.3 Typical operational day timeline

| Time | Activity |
|------|----------|
| 00:00 UTC | Automated daily health summary generated: fleet-wide battery SoH trends, anomaly count for past 24 hours, contact schedule compliance rate |
| Continuous | Telemetry ingestion, anomaly scoring, TimescaleDB writes, dashboard refresh — all automated, no operator action required unless an alarm fires |
| Per contact cycle (~95 min) | One or more satellites complete a pass. Operator reviews the contact report: telemetry volume downlinked, commands confirmed, anomalies detected |
| On alarm | Operator notified via dashboard. Reviews alarm detail — which satellite, which parameter, observed value, threshold, model confidence. Decides on response. |
| Daily | Review 24-hour telemetry trends. Check battery SoH trajectory. Review anomaly model performance metrics in MLflow. Confirm contact schedule for next 24 hours. |
| Weekly | Generate training data export for model retraining review. Compare current model performance against previous version. Promote improved model if metrics confirm improvement. |

---

## 9. Contact Window Operations

### 9.1 Contact window lifecycle

A contact window has four phases: pre-contact, AOS, active contact,
and LOS.

#### Pre-contact (T-minus planning phase)

Before a contact window begins, the ground station scheduler (Sprint 7)
has already:

1. Computed the AOS and LOS times using the SGP4 orbit model and the
   ground station's geographic coordinates and minimum elevation angle.
2. Resolved any antenna scheduling conflicts where multiple satellites
   are simultaneously visible (Scenario 3).
3. Loaded the command queue for uplink — all approved commands for this
   satellite in this window are queued and ready.
4. Published the contact schedule to the `passes.schedule` Kafka topic,
   displayed as a Gantt-style upcoming pass view in the dashboard.

#### AOS — Acquisition of Signal

At AOS, the satellite crosses above the minimum elevation angle. The
following automated actions occur within the first 30 seconds:

- The contact state machine transitions to `in_contact = True`.
- Downlink rate activates (~120 Mbps for the simulated X-band link).
- Uplink rate activates (~20 Mbps for command uplink).
- CPU utilisation increases (the OBC is actively managing downlink
  encoding).
- The dashboard contact indicator changes from NO to YES.
- The command uplink service begins transmitting the pre-loaded command
  queue.

#### Active contact

During the contact window (5–10 minutes for LEO; up to 8 hours for
Molniya Scenario 4), the operator can:

- Review incoming telemetry for anomalies that emerged since the last
  contact.
- Monitor command uplink acknowledgements — each command receives a
  telemetry confirmation that it was executed on board.
- Issue additional commands within the contact window.
- Monitor downlink data volume to confirm full payload data retrieval.

#### LOS — Loss of Signal

At LOS, the satellite drops below the minimum elevation angle. The
system automatically:

- Transitions `in_contact = False`.
- Resets downlink and uplink rates to 0.
- Generates a post-pass contact report: commands sent, commands
  confirmed, telemetry records received, anomalies detected.
- Writes the contact event to the TimescaleDB events table for the
  mission log.

### 9.2 Command approval chain

Commands follow a four-stage approval chain:

| Stage | Actor | Kafka topic | Description |
|-------|-------|------------|-------------|
| **Requested** | Operator or automated system | `commands.{sat}.requested` | Command proposed with full parameters |
| **Validated** | Validation service (automated) | `commands.{sat}.requested` | Parameter ranges checked; conflicts with current satellite state checked |
| **Approved** | Operator (human) | `commands.{sat}.approved` | Human confirms the command is appropriate — no automation can approve autonomously |
| **Uplinked** | Uplink service (automated) | `commands.{sat}.uplinked` | Command transmitted during next contact window; execution confirmation received |

The separation between validated and approved is deliberate. Validation
checks that the command parameters are within bounds and do not conflict
with the satellite's current state. Approval is the human decision that
the command is the right operational response. These are different
judgements and both are required.

---

## 10. Anomaly Detection and Response

### 10.1 How anomalies are detected

The anomaly detection service (Sprint 5) consumes `telemetry.{satellite_id}`
and applies an Isolation Forest model trained on normal telemetry for
the satellite's orbit type. The Isolation Forest learns the joint
distribution of normal telemetry — not just individual thresholds, but
the correlations between fields. A satellite whose battery is declining
during a sunlight pass (when the model expects charging) is anomalous
even if the battery value itself has not crossed a hard threshold.

The model scores each incoming record and publishes an `Alarm` record
to `alarms.{satellite_id}` when the anomaly score exceeds the configured
threshold. The Alarm record includes the satellite ID, timestamp, which
parameter triggered the detection, the observed value, the expected range,
and a severity classification.

### 10.2 Alarm severity levels

| Severity | Meaning | Required response |
|----------|---------|------------------|
| **INFO** | Statistically unusual but within physically plausible bounds. Model confidence is moderate. May be a transient or sensor artefact. | Note in the mission log. Monitor over the next two contact windows. No immediate action required. |
| **WARNING** | Clear deviation from normal pattern. Model confidence is high. Consistent with early-stage degradation of a subsystem. | Operator review within one contact window. Prepare a diagnostic command sequence for the next pass. Notify the system administrator. |
| **CRITICAL** | Severe deviation. Consistent with imminent subsystem failure or safe-mode trigger conditions. | Immediate operator action. Initiate the appropriate emergency procedure. Consider safe-mode commanding in the next available contact window. |

### 10.3 Response procedures by subsystem

#### Battery anomaly response

If the battery SoC is declining during sunlight passes:

1. Verify the `in_eclipse` flag. If `in_eclipse = True`, the discharge
   is expected — check if the alarm timing is consistent with eclipse entry.
2. Review the last 10 orbits of battery telemetry in the dashboard
   trending chart. Is the discharge rate accelerating? Is the minimum
   SoC per orbit getting lower cycle by cycle?
3. If the trend is confirmed, prepare a power budget review command for
   the next contact window: request the OBC to report individual
   subsystem power consumption.
4. If SoC drops below the safe-mode threshold (typically 15–20%), the
   OBC autonomously transitions to safe mode. All actions at this point
   focus on recovery: confirm safe mode entry, suspend non-essential
   payload operations, prioritise contact windows for recovery commanding.

#### Thermal anomaly response

If the spacecraft temperature is outside the expected range for the
current eclipse state:

1. Confirm whether the satellite is in eclipse or sunlight using the
   `in_eclipse` flag.
2. Compare observed temperature against the expected range for this
   orbital phase (eclipse: −20°C equilibrium target; sunlight: +35°C
   equilibrium target).
3. If temperature is rising above the sunlight equilibrium: check solar
   panel orientation telemetry (if available). Check if a payload
   instrument is unexpectedly powered on.
4. If temperature is failing to recover from eclipse (staying below
   −10°C in sunlight): check heater status. Consider commanding heater
   activation in the next contact window.

#### Communication anomaly response

If downlink or uplink rates are anomalous during a contact window:

1. Verify contact window timing — confirm AOS was correctly acquired.
   Check if the pass is within the minimum elevation angle window.
2. Check RF link budget parameters if available (Eb/N0, bit error rate).
3. If the link quality is degraded: check for interference, verify
   antenna pointing, consider requesting a contact at a different ground
   station for cross-check.

---

## 11. PhD Research Operations

ILMOP serves as the operational ground segment for a PhD research
programme on OFDM waveform design for Integrated Satellite Communication,
Navigation, and Remote Sensing (ISAC). Three distinct research
operational modes map to three specific constellation scenarios.

### 11.1 Communication demonstration (any scenario)

The communication demonstration — over-the-air transmission, reception,
and decoding of text and image signals — does not require specific
constellation geometry. It is conducted as a point-to-point link and
does not depend on multi-satellite visibility. Any ILMOP scenario
provides appropriate operational context.

The platform's role is supporting: confirming the satellite is in a
contact window (`in_contact = True`), logging the link quality
parameters (downlink and uplink rates), and providing the mission
operations context in which the demonstration occurs.

### 11.2 Navigation demonstration (Scenario 2)

The navigation demonstration requires Scenario 2: six satellites in
a single orbital plane, providing simultaneous visibility of four or
more satellites from the demonstration receiver — the minimum geometry
for three-dimensional trilateration.

The operational procedure is:

1. Start Scenario 2 to confirm all 6 satellites are generating telemetry
   and the constellation is operating correctly.
2. Identify a time window when 4 or more satellites are simultaneously
   above the receiver's horizon (elevation > 5°). The dashboard's
   simultaneous visibility counter (Sprint 6) supports this selection.
3. During the selected window, the OFDM waveform measures the timing
   offset (pseudo-range) from each visible satellite. The waveform's
   signal processing pipeline produces a position fix.
4. The NavigationTruthModel (poliastro HPOP, Sprint 6) computes the
   precise position of each satellite at the measurement epoch, with
   1–10 metre accuracy. This is the truth reference.
5. The navigation residual — the difference between the OFDM position
   fix and the HPOP truth — is logged to TimescaleDB via the navigation
   validation pipeline and accessible via `GET /navigation/{sat}/residuals`.
6. The GDOP is computed from the satellite positions and logged alongside
   the residual.

The use of HPOP rather than SGP4 as the truth reference is operationally
essential. SGP4 positional errors of 100–500 metres would make it
impossible to validate navigation claims below 200 metres — the truth
reference error would dominate the residual. The HPOP model's 1–10 metre
accuracy ensures that the residual reflects waveform performance rather
than reference error.

### 11.3 Remote sensing demonstration (Scenarios 3 and 4)

#### Mid-latitude remote sensing (Scenario 3)

The 24-satellite four-plane constellation provides multiple passes per
day over a fixed mid-latitude ground target. The operational procedure is:

1. Identify the ground target coordinates (latitude, longitude).
2. Use the coverage statistics tool (Sprint 6) to compute the daily pass
   schedule: how many times per day does a satellite pass within the
   waveform's sensing footprint?
3. For each pass, the OFDM waveform transmits a sensing signal toward
   the target and receives the backscatter. The pass geometry (look angle,
   incidence angle, range) is computed from the SGP4 orbit model at the
   measurement time.
4. Post-processing combines backscatter returns from multiple passes to
   build a coherent scene product.

#### Polar remote sensing (Scenario 4)

The Molniya constellation's long apogee dwell provides operational
conditions fundamentally different from LEO remote sensing:

1. Configure the Scenario 4 ground stations (Svalbard, Fairbanks) to
   maximise contact with the Molniya satellites during their northern
   apogee dwell.
2. A Molniya satellite at apogee moves at approximately 1.5 km/s
   (compared to ~7.5 km/s for a LEO satellite), allowing the waveform
   to illuminate a fixed polar target for an extended observation window
   — tens of minutes rather than seconds.
3. The extended dwell time enables coherent integration of backscatter
   returns over a long observation window, significantly improving
   signal-to-noise ratio.
4. SGP4 provides the look angle and range geometry at each measurement
   epoch. Note: SGP4 is accurate at Molniya apogee altitude (tens of
   kilometres from the truth at most), making it a valid geometry
   reference for remote sensing at this orbital phase. SGP4 is only
   inadequate as a navigation truth reference near perigee.

---

## 12. Operational Constraints and Limitations

### 12.1 Current capabilities (Sprints 1–4)

| Constraint | Impact | Resolution |
|-----------|--------|-----------|
| Contact model is timer-based, not geometry-driven | Contact window timing does not reflect actual pass geometry | Replaced by ILP ground station scheduler in Sprint 7 |
| No AI/ML anomaly detection yet | Anomalies visible only as threshold crossings, not pattern-based early warnings | Isolation Forest anomaly detection implemented in Sprint 5 |
| No command approval UI | The command pipeline exists but the operator-facing approval interface has not been built | Built in Sprint 5 as part of the alarm and response workflow |
| Single satellite only (no ConstellationManager) | The platform can only simulate and display one satellite at a time | ConstellationManager and four-scenario runner implemented in Sprint 5 |
| No HPOP navigation truth reference | Navigation validation residuals cannot be computed with the accuracy needed for peer-review-defensible claims | NavigationTruthModel (poliastro HPOP) implemented in Sprint 6 |
| Local deployment only | No remote access, no production SLA, no high availability | Azure AKS deployment in Sprint 7 |

### 12.2 Permanent design constraints

The following constraints are by design — they are not limitations to be
resolved in future sprints but deliberate architectural decisions:

- **LEO and Molniya telemetry are never combined in one ML training
  dataset.** This is enforced through the `orbit_type` field in the
  Telemetry schema and the TimescaleDB query filters used in training
  data export.

- **The HPOP NavigationTruthModel is used only for the navigation
  validation pipeline.** SGP4 is used for all operational purposes —
  contact scheduling, eclipse detection, ground track visualisation.

- **All commands require human approval before uplink.** No automated
  system can approve a command independently, regardless of urgency
  or operational context.

- **The Streamlit dashboard is a pure API client.** It never connects
  to TimescaleDB directly. All data access goes through the FastAPI layer.

---

## 13. Interfaces

### 13.1 Internal interfaces

| Interface | From | To | Protocol / mechanism |
|-----------|------|----|---------------------|
| Telemetry stream | Simulator / Producer | Kafka | JSON, Pydantic v2, topic `telemetry.{sat_id}` |
| Telemetry persistence | Kafka | TimescaleDB sink | psycopg2, batched INSERT, idempotent |
| Alarm publication | Anomaly detection | Kafka | JSON Alarm schema, `alarms.{sat_id}` |
| REST API | TimescaleDB / Redis | FastAPI | asyncpg / redis.asyncio |
| Dashboard data | FastAPI | Streamlit | HTTP GET, requests library |
| Latest record cache | FastAPI | Redis | JSON, 5s TTL, setex/get |
| Pass schedule | ILP Scheduler | Kafka | JSON, `passes.schedule` topic |

### 13.2 External interfaces (Sprint 7)

| Interface | External system | Direction | Purpose |
|-----------|----------------|-----------|---------|
| Azure Event Hubs | Azure cloud | Bidirectional | Kafka-compatible managed streaming — no code changes required |
| Azure PostgreSQL | Azure cloud | Bidirectional | Managed TimescaleDB with automatic backup and HA |
| Space-Track.org | US Space Force | Inbound (future) | Real satellite TLEs for production operations with actual spacecraft |
| IGS data centres | International GNSS Service | Inbound | SP3 precise ephemeris files for NavigationTruthModel |

---

## 14. Assumptions and Dependencies

### 14.1 Assumptions

1. The satellite simulator faithfully represents the operational behaviour
   of a real LEO satellite for the purposes of anomaly detection model
   training. The eclipse-aware physics models produce telemetry with
   correlation structure that a real spacecraft would exhibit.

2. SGP4 orbit propagation is accurate enough for operational purposes
   (contact scheduling, eclipse detection, ground track visualisation)
   at LEO altitudes. Sub-kilometre positional accuracy is not required
   for these applications.

3. The Isolation Forest anomaly detection model, trained on Scenario 1–3
   telemetry, will generalise to anomaly patterns not present in the
   training data, provided those anomalies represent meaningful deviations
   from the eclipse-correlated normal baseline.

4. LEO and Molniya HEO telemetry signatures are sufficiently different
   that training a single model on both data types would produce a baseline
   suitable for neither. This assumption motivates the `orbit_type`
   separation architecture.

5. The HPOP NavigationTruthModel (poliastro with EGM2008 + NRLMSISE-00)
   achieves 1–10 metre positional accuracy for LEO circular orbits over
   observation windows of less than one hour — sufficient to validate
   navigation claims in the 50–200 metre range from the OFDM waveform.

### 14.2 Dependencies

| Dependency | Type | Required for |
|-----------|------|-------------|
| Docker Desktop | Infrastructure | Kafka, TimescaleDB, Redis — all development operations |
| sgp4 library (v2.27) | Python package | Orbit propagation, eclipse detection, all scenarios |
| Apache Kafka (KRaft) | Infrastructure | All inter-service telemetry flow from Sprint 3 onward |
| TimescaleDB (pg16) | Infrastructure | Telemetry persistence, training data export, API queries |
| poliastro | Python package | NavigationTruthModel — required for PhD navigation demonstration (Sprint 6) |
| PuLP | Python package | ILP ground station scheduler (Sprint 7) |
| MLflow | Python package | Anomaly detection experiment tracking (Sprint 5) |
| Azure subscription | Cloud platform | Production cloud deployment (Sprint 7) |
| IGS SP3 ephemeris files | External data | Lighter-weight alternative to full HPOP for navigation truth (Sprint 6) |

---

## 15. Future Operational Capabilities

### 15.1 Sprint 5 — Intelligent operations

- **Real-time anomaly detection:** the Isolation Forest model scores every
  incoming telemetry record and the alarm pipeline delivers WARNING and
  CRITICAL severity alerts to the operator dashboard within seconds of
  the triggering event.

- **Fault injection:** the simulator can be configured to inject synthetic
  faults (battery degradation, thermal runaway, safe-mode trigger) into
  the telemetry stream for model validation. Fault injection is always
  an explicit operator action — it cannot occur accidentally in normal
  operations.

- **Multi-satellite fleet operations:** the ConstellationManager enables
  simultaneous simulation and monitoring of all four constellation
  scenarios, with the dashboard displaying a fleet-wide health summary
  and the ability to drill into any individual satellite.

- **Training data generation at scale:** the `--speed` parameter enables
  training datasets to be generated orders of magnitude faster than real
  time, making weekly model retraining practical.

### 15.2 Sprint 6 — Demonstration operations

- **Precision navigation truth:** the NavigationTruthModel (poliastro
  HPOP) provides 1–10 metre positional accuracy for the PhD navigation
  validation pipeline, enabling peer-review-defensible navigation
  accuracy claims.

- **Fleet dashboard:** all active satellites are displayed simultaneously
  on a world map, with colour-coded health status and orbit-type
  differentiation. Coverage statistics (contact fraction, revisit time,
  simultaneous visibility) are computed and displayed per scenario.

- **Four-scenario progressive demonstration:** the complete demonstration
  sequence from one satellite to 24, followed by the standalone Molniya
  polar demonstration, is runnable end-to-end as a coherent operational
  narrative.

### 15.3 Sprint 7 — Cloud-native and autonomous operations

- **ILP ground station scheduler:** the contact window assignment problem
  for 24 satellites and 5 ground stations is solved by a PuLP integer
  linear programming formulation, replacing the random-duration timer
  model. The resulting schedule is published to Kafka and consumed by
  the dashboard, producer, and command queue.

- **Azure cloud deployment:** the platform migrates from local Docker
  Compose to Azure AKS with managed Event Hubs (Kafka-compatible) and
  Azure Database for PostgreSQL (TimescaleDB). Application code is
  unchanged — only environment variables change.

- **Molniya standalone operations:** Scenario 4 is operated end-to-end
  as a standalone mission with its own scheduler (tuned for long-dwell
  contact windows), its own HEO_MOLNIYA anomaly detection model, and
  dedicated polar ground stations.

---

*— End of Document —*

*DOC-003 | ILMOP Concept of Operations | Version 1.0 | 2026-09-08*
