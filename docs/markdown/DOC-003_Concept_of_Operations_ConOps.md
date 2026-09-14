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
| Primary purpose | Pipeline validation; anomaly detection baseline training; communication demonstration baseline |

Scenario 1 is the operational baseline from which all subsequent
scenarios are extensions. It establishes the telemetry pattern —
regular eclipse cycling, battery charge and discharge with the orbital
period, thermal oscillation — that the anomaly detection model learns
as normal. Any operational scenario involving a single satellite, whether
for initial platform testing, PhD communication demonstration, or baseline
model training, uses Scenario 1.

### 6.2 Scenario 2 — Single-plane LEO constellation (6 satellites)

| Parameter | Value |
|-----------|-------|
| Satellites | 6 (SAT-A1 through SAT-A6) |
| Orbit type | LEO circular, 550 km, 53° inclination |
| Plane configuration | 1 orbital plane, RAAN = 45°, satellites spaced 60° in mean anomaly |
| Contact frequency | One pass per ~16 minutes per ground station (6× improvement over Scenario 1) |
| Simultaneous visibility | Typically 1 satellite per pass; brief dual-satellite overlap (~5 min) when consecutive satellites share the visibility window. Geometry insufficient for GPS-style simultaneous trilateration — see Scenario 3. |
| ML model | LEO_CIRCULAR model extended with 6-satellite training data |
| Primary purpose | Navigation demonstration; contact frequency improvement; multi-satellite coordination |

Scenario 2 supports two navigation demonstration modes that exploit its
higher contact frequency (one pass per ~16 minutes instead of ~95
minutes): Doppler-based ranging from individual passes, and sequential
accumulated ranging across multiple successive passes within a short
observation window. With satellites spaced 60° apart, the 37° ground
visibility arc at 550 km altitude means only 1–2 satellites are
simultaneously visible — insufficient for GPS-style trilateration, which
requires 4+ satellites distributed across different azimuths.
Simultaneous multi-satellite trilateration is demonstrated in Scenario 3,
where four planes at different RAANs place satellites across the full sky.

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

### 9.3 Antenna tracking — operational reality and ILMOP simplification

#### What real LEO ground stations do

In a real LEO ground station, the antenna **must** auto-track the
satellite throughout the entire contact window. This is a fundamental
operational requirement of LEO communications that distinguishes it
from GEO operations.

A GEO satellite at 35,786 km appears essentially stationary from the
ground — a dish can be fixed in position for days. A LEO satellite at
550 km moves at approximately 7.5 km/s and sweeps across the visible
sky in 5–10 minutes, covering 60–120° of angular travel per pass. The
angular slew rate peaks at 3–5°/second near the highest elevation point
of an overhead pass. An antenna that stops tracking even briefly loses
the link entirely.

A production ground station tracking system performs three functions
throughout the pass:

**Mechanical pointing** — a two-axis motorised mount (azimuth and
elevation) drives the antenna along the predicted pass trajectory,
typically pre-loaded as a time-stamped pointing schedule computed from
the satellite's orbital elements (programme track). High-gain narrow-beam
systems also use RF-derived feedback (autotrack / monopulse) to correct
residual pointing errors in real time.

**Doppler compensation** — the satellite's radial velocity produces a
Doppler frequency shift of up to ±50 kHz on a typical LEO downlink at
S-band. The receiver tunes continuously to follow this shift throughout
the pass. The uplink frequency is pre-compensated so the satellite
receives commands at its nominal frequency regardless of the Doppler
offset at each moment in the pass.

**AOS/LOS detection** — the tracking controller monitors received signal
strength to detect the actual acquisition and loss of signal moments,
which may differ slightly from the predicted AOS/LOS times due to
atmospheric effects, TLE age, and terrain masking.

The pre-contact planning step that loads the pointing schedule is a real
operational activity. For the Sprint 7 ground station scheduler, computing
AOS and LOS times from SGP4 orbital mechanics is the prerequisite for
generating this schedule.

#### ILMOP simplification — what is and is not modelled

ILMOP abstracts antenna tracking completely. The `_ContactModel` state
machine transitions `in_contact = True` at the predicted AOS time and
`in_contact = False` at the predicted LOS time. No pointing schedule,
no tracking accuracy model, no Doppler compensation, and no link budget
calculation are implemented.

This is the correct simplification for ILMOP's purpose. ILMOP is a
health monitoring and anomaly detection platform, not an RF link budget
tool. The `downlink_rate_mbps` and `uplink_rate_mbps` fields assume
the link is established and performing nominally — which implicitly
assumes tracking is working correctly.

The only tracking-related anomalies ILMOP would detect are:
- An unexpectedly short pass (contact drops before predicted LOS) —
  appears as an early `in_contact = False` transition, anomalous
  relative to the expected pass duration model
- Unexpectedly degraded link rates during a pass — appears as lower
  than normal `downlink_rate_mbps`, detectable by the Isolation Forest
  model

**Note for publications** (Paper 4 — cloud-native ground segment,
Paper 2 — digital twin framework): include the following statement in
the ground segment architecture section to pre-empt reviewer questions:

> *"The contact window model abstracts antenna tracking, Doppler
> compensation, and link budget calculations. The `in_contact` flag
> represents the operational state of the contact window — link
> established and performing nominally — without modelling the
> mechanical and RF processes that establish and maintain it. This
> abstraction is appropriate for a health monitoring platform; a
> full ground station simulator would incorporate a pointing schedule
> generator, a tracking accuracy model, and a link budget calculator
> as separate services consuming the SGP4 orbital truth provided by
> the orbit model."*

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

### 11.2 Navigation demonstration (Scenarios 1, 2, and 3)

The OFDM waveform navigation capability is demonstrated progressively
across three constellation scenarios, each supporting a different
navigation mode of increasing geometric complexity. This mirrors the
historical development of satellite navigation — from single-satellite
Doppler systems (TRANSIT, 1964) through multi-satellite GPS — while
demonstrating a modern integrated OFDM ISAC implementation.

#### Navigation mode 1 — Doppler ranging (Scenario 1: single satellite)

A single LEO satellite pass produces a characteristic Doppler frequency
shift as the satellite approaches, passes overhead, and recedes. The
OFDM waveform measures this shift continuously across the pass. From
the Doppler curve and the known satellite orbit, a 2D position fix
(latitude and longitude) is derived — the operational principle of the
TRANSIT system, here demonstrated with a modern ISAC waveform.

Procedure:

1. Run Scenario 1; identify a pass with elevation above 20°.
2. Record the Doppler frequency shift across the full pass duration.
3. Apply the Doppler navigation algorithm to derive a 2D position fix.
4. Compare against NavigationTruthModel (poliastro HPOP, 1–10 m accuracy).
5. Log position residual and pass geometry to TimescaleDB.

#### Navigation mode 2 — Sequential accumulated ranging (Scenario 2: 6 satellites, single plane)

With six satellites, a pass arrives every ~16 minutes. Ranging
measurements are accumulated across multiple successive passes within a
1–2 hour observation window. Satellite positions change between passes,
providing geometric diversity over time that enables a 3D position fix
from accumulated pseudoranges even though only 1–2 satellites are
visible at any single moment.

Procedure:

1. Run Scenario 2; select a 2-hour observation window.
2. For each satellite pass, measure the pseudorange using the OFDM waveform.
3. Accumulate pseudoranges — each pass adds one equation with a different satellite geometry.
4. Solve the accumulated pseudorange system for a 3D position fix.
5. Validate against NavigationTruthModel; compute position residual RMS.

#### Navigation mode 3 — Simultaneous multi-satellite trilateration (Scenario 3: 24 satellites, 4 planes)

The four-plane constellation places satellites at RAANs of 0°, 90°,
180°, and 270°, meaning satellites arrive from four different azimuth
directions around the horizon. At any given moment, 4 or more satellites
from different planes are visible simultaneously and distributed across
the sky — the geometry required for GPS-style 3D trilateration with
good GDOP. This is the closest analogue to operational GNSS demonstrated
by ILMOP.

Procedure:

1. Run Scenario 3; use the dashboard simultaneous visibility counter
   (Sprint 6) to identify a window with 4+ satellites visible from
   different azimuth directions (elevation > 5°).
2. Simultaneously measure the pseudorange to each visible satellite
   using the OFDM waveform.
3. Solve the pseudorange system for a 3D position fix.
4. NavigationTruthModel (poliastro HPOP) provides satellite positions
   to 1–10 metre accuracy — the truth reference.
5. Log the navigation residual via `GET /navigation/{sat}/residuals`.
   Compute GDOP alongside — low GDOP confirms geometric quality;
   high GDOP explains larger residuals without invalidating the waveform.

#### Why HPOP rather than SGP4 as the truth reference (all modes)

SGP4 positional errors of 100–500 metres make it impossible to validate
navigation claims below 200 metres — the truth reference error dominates
the residual. The HPOP NavigationTruthModel (1–10 metre accuracy)
ensures the residual reflects waveform performance, not reference error.
This is essential for peer-review credibility. SGP4 is retained for all
operational purposes (contact scheduling, eclipse detection, simulation);
only the navigation truth reference uses HPOP.

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

### 15.1 Sprint 5 — Intelligent operations ✅ Complete

The following capabilities are now operational as of Sprint 5 (v0.5.0-beta):

- **Real-time anomaly detection:** the Isolation Forest model scores every
  incoming telemetry record. The alarm pipeline delivers WARNING and
  CRITICAL severity alerts to the operator dashboard within seconds of
  the triggering event. Run with:
  `python -m services.anomaly_detection.detector`

- **Fault injection:** the simulator injects synthetic faults (battery
  degradation, thermal runaway, safe-mode trigger) into the telemetry
  stream for anomaly detection model validation. Fault-injected records
  are labelled `fault_injected=True` and excluded from normal training
  data. Fault injection is always an explicit operator action via the
  `--fault` flag — it cannot occur accidentally in normal operations.

- **Multi-satellite fleet operations:** the `ConstellationManager` and
  four constellation YAML files enable any of the four scenarios to be
  launched from a single command (`run_demo.py`). The `--speed` parameter
  enables training data generation at up to 3600× real time.

- **MLflow experiment tracking:** every training run logs parameters,
  metrics, and model artifacts. Models are registered in the MLflow
  registry as `ilmop-anomaly-leo_circular` and `ilmop-anomaly-heo_molniya`
  and loaded by the detector service at startup.

See ADR-014 (Isolation Forest), ADR-015 (MLflow), and ADR-016
(LEO/HEO training data separation) for the full rationale.

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

---

## 16. Telemetry Continuity and Contact Window Behaviour

### 16.1 What happens to telemetry between contact windows

In a real LEO spacecraft, the onboard computer generates telemetry
continuously at all times — regardless of whether a ground station is
in range. This data is stored in onboard mass memory (a solid-state
recorder) as **stored telemetry**. When a contact window opens, two
streams flow simultaneously to the ground:

- **Real-time telemetry** — the current state of the spacecraft, streamed live
- **Stored telemetry dump** — the full history since the last contact, filling the inter-pass gap

The ground segment reconstructs a complete, uninterrupted timeline
across the full orbital period. No telemetry is lost between passes.

### 16.2 How ILMOP models this

ILMOP's simulator runs on the ground and generates telemetry at 1 Hz
continuously. Every record is published to Kafka and written to
TimescaleDB regardless of the `in_contact` flag. The `in_contact`
field is a **status indicator** — it models the satellite's operational
contact state; it does not gate the telemetry flow to the pipeline.

```
in_contact = True  →  telemetry flows to Kafka and TimescaleDB (continuous)
in_contact = False →  telemetry flows to Kafka and TimescaleDB (continuous)
```

This is correct and intentional for two reasons:

**The simulator is not an onboard system.** It runs on a development
machine, not a satellite 550 km overhead. There is no radio link and
no concept of data that cannot be received because the satellite is out
of range. The `in_contact` flag simulates the operational state of the
satellite — its link modes and rates — not a physical RF gate.

**Between-pass telemetry is essential for AI/ML training.** The most
significant anomaly patterns occur between contact windows: battery
draining faster than expected during eclipse, temperature failing to
recover in sunlight. Training only on the ~10% of telemetry generated
during contact windows produces a model blind to the physics it most
needs to detect.

The contact state is correctly represented in the telemetry fields:

| Field | During contact | Out of contact |
|---|---|---|
| `in_contact` | `True` | `False` |
| `downlink_rate_mbps` | ~120 Mbps | 0.0 |
| `uplink_rate_mbps` | ~20 Mbps | 0.0 |
| `cpu_utilization_pct` | Elevated (downlink encoding) | Baseline |

### 16.3 Known limitation — stored telemetry latency not modelled

In a production deployment, telemetry received at the start of a
contact window is already up to 90 minutes old — generated during the
previous non-contact period and stored onboard until the pass. An
anomaly that occurred 45 minutes ago is only discovered when the next
contact window opens and the stored dump arrives.

ILMOP does not currently model this **stored telemetry latency**. The
anomaly detection service operates on a continuous 1 Hz stream rather
than bursts of stored data arriving at each contact window. This does
not affect the validity of the simulation results — the complete
timeline is always available for training and validation. It is a known
gap relative to production operations and is noted here for completeness.

**Note for publications** (Papers 2 and 3): include the following
statement in the methodology section to pre-empt reviewer questions:

> *"The simulator generates telemetry continuously at 1 Hz across all
> orbital phases. The `in_contact` flag and associated link rate fields
> model the satellite's contact state without gating the telemetry flow
> to the ground processing pipeline, consistent with the physical
> behaviour of a real spacecraft where onboard data recorders preserve
> telemetry across non-contact periods for downlink during the next
> pass. The current implementation does not model stored telemetry
> latency; in a production deployment, the anomaly detection service
> would operate on telemetry arriving in bursts at contact time rather
> than as a continuous stream."*

---

*Document version: 1.3 — Section 15.1 promoted to current; Sections 16 added; Sections 6.2 and 11.2 corrected*
*Previous version: 1.2 — Section 9.3 antenna tracking added*

---

## 17. Simulation Fidelity Assumptions and Known Limitations

This section is the authoritative register of simplifying assumptions
applied throughout ILMOP, organised by system layer. These assumptions
are appropriate for a research and demonstration platform and are
explicitly bounded here so that results can be correctly interpreted,
cited, and defended under peer review or PhD examination.

The methodology sections of Papers 2, 3, 6, and 7 should each cite this
section and state the subset of assumptions relevant to that paper's
results.

### 17.1 Orbital mechanics

- **SGP4 with fixed elements.** 100–500 m accuracy for LEO circular
  orbits; km-level near Molniya perigee. No manoeuvre modelling, no
  epoch decay, no J5+ harmonics, no ocean tides, no relativistic
  corrections. The HPOP NavigationTruthModel (1–10 m, Sprint 6) is
  used only as the navigation truth reference — not for operational
  orbit computation.
- **No eclipse penumbra.** Cylindrical shadow model produces a sharp
  step at eclipse entry and exit. Real transition is a 1–2 minute ramp.
- **Circular orbit approximation.** Eccentricity set to 0.001 for
  Scenarios 1–3; real constellation satellites have non-zero
  eccentricities producing small per-orbit altitude variations.

### 17.2 Spacecraft physics

- **Single-node thermal model.** One temperature value for the entire
  spacecraft; no spatial variation, no subsystem-level thermal coupling.
- **Idealised battery.** Fixed charge/discharge rates; no capacity fade,
  no temperature-dependent efficiency, no voltage sag. Degradation
  exists only as explicit fault injection.
- **Constant solar panel output.** No panel radiation degradation
  (~1–3%/year in reality), no solar incidence angle variation.
- **No attitude control.** No ADCS power consumption, no reaction wheel
  or magnetorquer modelling, no safe-mode attitude dynamics.
- **No radiation environment.** Single-event upsets and
  radiation-induced anomalies are not modelled — the anomaly detection
  model does not train on this anomaly class.

### 17.3 Ground segment and communications

- **Binary contact model.** Perfect link assumed for the full pass
  duration — no link budget, pointing loss, rain fade, Doppler
  degradation, or interference. Replaced by geometry-driven ILP
  scheduler in Sprint 7.
- **No uplink command latency.** Commands take effect instantaneously;
  real round-trip latency is seconds to minutes.
- **No stored telemetry latency.** Anomaly detection operates on a
  continuous 1 Hz stream, not on burst arrivals at contact windows.
  See Section 16 for the reviewer-ready methodology statement.

### 17.4 Anomaly detection and machine learning

- **Stationarity assumption.** Isolation Forest trained on stationary
  data — spacecraft behaviour evolves over a real mission lifetime.
  Periodic retraining required; automated drift detection is a future
  capability.
- **No operating mode awareness.** One continuous operational mode
  simulated; mode transitions can cause false positives in a real system.
- **Synthetic fault representativeness.** Fault injection uses linear
  approximations of real failure mechanisms — real degradation follows
  complex physical models. A model validated only on synthetic faults
  may not generalise to all real failure trajectories.

### 17.5 Navigation demonstration

- **Single-frequency, code-phase (pseudorange) only.** Ionospheric
  delay (2–15 m) and tropospheric delay (2–25 m) are not eliminated.
  Hardware delay biases are not modelled. Carrier-phase (centimetre-
  level) accuracy is not claimed — expected accuracy is metres to tens
  of metres. These must be stated explicitly in the navigation paper
  error budget.

### 17.6 System-level

- **No fault tolerance.** Each service runs as a single instance with
  no redundancy — appropriate for a research platform, must be stated
  in publications.
- **Simulated time vs wall-clock.** `--speed` timestamps are simulated
  mission time, not real dates. Time-based analysis is valid; absolute
  timestamps are not real mission dates.
- **No security model (Sprints 1–6).** No API authentication, no Kafka
  TLS, default database credentials. Addressed in Sprint 7 Azure
  deployment.

### 17.7 Summary table

| Layer | Assumption | Impact | Mitigation |
|---|---|---|---|
| Orbital | SGP4, 100–500 m accuracy | Navigation truth limited | HPOP for navigation (Sprint 6) |
| Orbital | No manoeuvres or epoch decay | Orbit does not drift | Out of scope for Sprint 1–7 |
| Orbital | No eclipse penumbra | Step vs ramp at eclipse boundary | Minor; acceptable for training data |
| Physics | Single-node thermal | No subsystem thermal anomalies | Stated in papers |
| Physics | Idealised battery | No capacity fade in normal data | Fault injection covers degradation |
| Physics | No radiation environment | Radiation anomalies undetectable | Stated as out of scope |
| Ground | Binary contact model | No link quality anomalies | ILP scheduler in Sprint 7 |
| Ground | No stored telemetry latency | Continuous stream vs burst arrival | ConOps Section 16 |
| ML | Stationarity | Model drift not detected | Periodic retraining |
| ML | No mode awareness | False positives on transitions | Mode-conditional detection future |
| Navigation | Single-freq, code-phase | Ionospheric + tropospheric errors | Stated in navigation paper error budget |
| System | No redundancy | Single point of failure | Stated in publications |

---

*Document version: 1.4 — Section 17 added: Simulation Fidelity Assumptions and Known Limitations*
*Previous version: 1.3 — Section 15.1 promoted to current operational*
