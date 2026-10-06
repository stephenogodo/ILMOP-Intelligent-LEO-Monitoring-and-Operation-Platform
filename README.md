# ILMOP — Intelligent LEO Monitoring and Operation Platform

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.7.0--beta-blue" alt="version"/>
  <img src="https://img.shields.io/badge/python-3.14%2B-brightgreen" alt="python"/>
  <img src="https://img.shields.io/badge/tests-515%20passing-brightgreen" alt="tests"/>
  <img src="https://img.shields.io/badge/sprint%207-5%20of%207%20layers%20complete-orange" alt="sprints"/>
  <img src="https://img.shields.io/badge/licence-MIT-lightgrey" alt="licence"/>
</p>

ILMOP is a full-stack, production-grade LEO Digital Twin and Mission Operations System (MOS)
for Low Earth Orbit (LEO) satellite constellations, built from first
principles in Python. It demonstrates the complete operational chain of
modern commercial satellite operations: physics-accurate spacecraft
simulation, real-time event streaming, time-series persistence, REST API
delivery, AI-assisted anomaly detection, and ILP-optimised ground contact
scheduling — all running as independent, loosely coupled microservices
connected by an Apache Kafka event backbone.

The platform is simultaneously a **software engineering portfolio project**
demonstrating scalable cloud-native architecture, a **research instrument**
providing the operational ground segment for a PhD programme in OTFS-ISAC
waveform design for Integrated Satellite Communication, Navigation, and Remote
Sensing (ISAC), and a **progressive scalability demonstration** showing the same
architecture handling one satellite through a 24-satellite multi-plane
constellation without structural change. The research instrument role is backed
by a dedicated waveform layer implementing the complete OTFS signal model
(ISFFT/Heisenberg transmitter, Wigner-Ville receiver), a novel three-function
pilot frame that simultaneously enables broadband communication, pseudorange
navigation, and target echo sensing from a single pilot symbol, and an adaptive
orbital guard region sized to the satellite's instantaneous Doppler shift.
Navigation accuracy is validated at 9.99 m RMS pseudorange (Cambridge, 52.2°N)
and 8.87 m (Lagos, 6.5°N) against a J2–J6 high-precision orbital truth
reference. The waveform layer carries 129/129 passing tests; combined platform
and waveform tests total **410/410 with zero warnings** through Sprint 6.

**Sprint 7 adds 105 new tests** (Layers 1–5 complete) for a running total of
**515 passing tests** across the platform.

## Why this project exists

Modern commercial LEO constellations — communications networks,
Earth observation fleets, IoT coverage platforms — require ground
software architectures that are fundamentally different from the
monolithic, point-to-point systems designed for legacy GEO satellites.
A constellation of 24 satellites generating telemetry at 1 Hz produces
86,400 records per satellite per day, across 24 satellites, requiring
real-time anomaly detection and automated scheduling decisions that no
team of operators can make manually at that rate.

ILMOP is built around this problem. Every architectural decision — Kafka
over point-to-point messaging, TimescaleDB over plain PostgreSQL, SGP4
orbit propagation with eclipse-aware physics, Isolation Forest anomaly
detection trained on physically correlated telemetry — is driven by the
operational realities of LEO constellation management, not by tutorial
convenience. All decisions are documented with full rationale and
alternatives considered in the Architecture Decision Record (ADR)
register in `docs/adr/`.

---

## Architecture

### The seven-layer pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 7b — Coverage Statistics   Streamlit, contact fraction,      │
│                                   revisit time, simultaneous sats    │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 7a — Fleet Dashboard       Plotly world map, all 4 scenarios │
│                                   7 ground stations, pass schedule   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 6b — Navigation Validation OTFS pseudorange vs HPOP truth    │
│                                   9.99 m RMS (Cambridge)            │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 6a — OTFS Waveform         Channel emulator (FSPL + Rician   │
│                                   + Doppler + AWGN), OrbitalFrame   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 6 — AI/ML Inference        Isolation Forest + MLflow         │
│                                   per-orbit-type model routing       │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 5 — Storage                TimescaleDB hypertable            │
│                                   Redis latest-record cache          │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 4 — Kafka Event Backbone   KRaft, topic-per-satellite        │
│                                   satellite_id as message key        │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3 — Serialisation          Pydantic v2 Telemetry schema      │
│                                   JSON, versioned, validated         │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2 — Ground Segment         ILP contact window scheduler      │
│                                   Handover state machine (Sprint 7) │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1 — Space Segment          SGP4 orbit propagation            │
│            (Simulator)            Eclipse-aware physics models       │
└─────────────────────────────────────────────────────────────────────┘
          ↑ telemetry flows up              ↓ commands flow down
```

### Sprint 7 — LEO Constellation Operations Suite

Sprint 7 extends ILMOP from a monitoring and anomaly-detection platform
into a full LEO constellation operations system. It adds five tightly
coupled operational capabilities built in seven layers:

| Layer | Module | Purpose | Status | Tests |
|-------|--------|---------|--------|-------|
| 1 | `geometry.py` | AOS/LOS from SGP4 + WGS84 geometry | ✅ Complete | 13 |
| 2 | `contact_window.py` | ILP contact window scheduler (PuLP/CBC) | ✅ Complete | 16 |
| 3 | `isl_visibility.py` | ISL line-of-sight topology model | ✅ Complete | 18 |
| 4 | `handover.py` | Handover state machine (IDLE→ACQUIRING→ACTIVE→HANDING_OVER→RELEASED) | ✅ Complete | 34 |
| 5 | `dark_satellite.py` | Dark satellite detection + dark interval timeline | ✅ Complete | 24 |
| 6 | `relay_coordinator.py` | Store-and-forward ISL relay coordinator | ⏳ In progress | — |
| 7 | Dashboard integration | Live ops metrics, ILP composite objective | ⏳ Pending | — |

### Key architectural decisions

**Event-driven over point-to-point.** All inter-service communication
flows through Kafka topics. The simulator has no knowledge of how many
consumers exist or what they do with the data. Fan-out, replay, and
per-satellite ordering are free consequences of this design.

**Schema-first.** The `Telemetry` Pydantic model is the single data
contract across all pipeline components.

**Physics before statistics.** The satellite simulator produces
eclipse-correlated telemetry. An anomaly detection model trained on
random-walk telemetry learns nothing operationally meaningful.

**Separation of operational and precision orbit models.** SGP4 (fast,
100–500 m accuracy) is used for scheduling and simulation. HPOP (J2–J6,
~3–5 m accuracy) is used exclusively as the PhD navigation truth reference.

---

## Project status

| Sprint | Version | Title | Status | Tests |
|--------|---------|-------|--------|-------|
| 1 | v0.2.0-alpha | Dynamic Spacecraft Telemetry Simulator | ✅ Complete | 43/43 |
| 2 | v0.2.1-alpha | Simulator Physics Upgrade | ✅ Complete | 43/43 |
| 3 | v0.3.0-alpha | Streaming Telemetry Platform | ✅ Complete | 57/57 |
| 4 | v0.4.0-beta | Telemetry Data Platform — API and Dashboard | ✅ Complete | 75/75 |
| 5 | v0.5.0-beta | Intelligent Digital Twin — AI/ML | ✅ Complete | 152/152 |
| 6 | v0.6.0-beta | Demonstration Layer — OTFS, HPOP, Fleet Dashboard | ✅ Complete | 281/281 |
| **7** | **v1.0.0** | **LEO Constellation Operations Suite** | **⬅ In progress (5/7 layers)** | **105/—** |

Full sprint scope, risk register, ADR register, and session log:
[`docs/ILMOP_Project_Tracker.md`](docs/ILMOP_Project_Tracker.md)

---

## Constellation design

ILMOP implements four progressive demonstration scenarios:

### Scenario 1 — Single satellite
One satellite in an ISS-class 550 km, 51.6° inclination orbit.

### Scenario 2 — Single-plane LEO constellation (6 satellites)
Six satellites equally spaced in one orbital plane. Contact fraction from Cambridge: ~45.5%.

### Scenario 3 — Multi-plane LEO constellation (24 satellites, 4 × 6)
Four orbital planes, 90° RAAN separation, six satellites per plane.
Creates antenna scheduling conflicts that the Sprint 7 ILP scheduler resolves.

### Scenario 4 — Molniya HEO polar constellation (6 satellites, standalone)
Six satellites in Molniya orbits (63.4° inclination, eccentricity 0.74,
apogee ~39,750 km). Continuous polar coverage with dual-satellite visibility
from Svalbard (78.2°N) and Fairbanks (64.8°N).

---

## Technology stack

| Layer | Technology | Version | Decision |
|-------|-----------|---------|----------|
| Language | Python | 3.14+ | ADR-001 |
| Schema validation | Pydantic | v2.13+ | ADR-002 |
| Configuration | pydantic-settings | 2.15+ | ADR-010 |
| Event streaming | Apache Kafka (KRaft) | latest | ADR-003 |
| Kafka client | confluent-kafka | 2.15+ | ADR-005 |
| Time-series DB | TimescaleDB (PostgreSQL 16) | pg16 | ADR-004 |
| DB driver — sink | psycopg2-binary | 2.9+ | ADR-011 |
| DB driver — API | asyncpg | 0.31+ | ADR-012 |
| Orbit propagation | sgp4 | 2.27 | ADR-006 |
| HPOP truth model | poliastro 0.7.0 + scipy + numba | Sprint 6 | ADR-017 |
| OTFS channel model | numpy + scipy | Sprint 6 | ADR-018 |
| REST API | FastAPI + Uvicorn | 0.141+ | ADR-012 |
| Dashboard | Streamlit | 1.63+ | ADR-013 |
| Cache | Redis | 7-alpine | — |
| ML / tracking | scikit-learn + MLflow | 1.9 / 3.16 | ADR-014/015 |
| ILP scheduler | PuLP 2.9.0 (CBC) | Sprint 7 | ADR-020 |
| Cloud | Azure AKS + Event Hubs | Sprint 8 | ADR-019 |
| Testing | pytest | 9.1+ | — |

---

## Quickstart

### Prerequisites
- Python 3.14+
- Docker Desktop
- Git

### 1. Clone and create virtual environment

```bash
git clone https://github.com/stephenogodo/ILMOP-Intelligent-LEO-Monitoring-and-Operation-Platform.git
cd ILMOP-Intelligent-LEO-Monitoring-and-Operation-Platform
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Run the test suite

```bash
python -m pytest tests/ -v
# Expected: 515 passed (Sprint 7 Layers 1–5 + Sprint 1–6)
```

### 3. Start infrastructure

```bash
docker compose --profile streaming --profile database --profile cache up -d
```

### 4. Run the operational pipeline

```bash
python run_demo.py --scenario 1         # 1 satellite, real time
python run_demo.py --scenario 3 --speed 60  # 24 satellites, 4 planes
```

---

## Architecture Decision Records

| ADR | Decision |
|-----|----------|
| ADR-001 | Python as primary implementation language |
| ADR-002 | Pydantic v2 for telemetry schema validation |
| ADR-003 | Apache Kafka as the event streaming backbone |
| ADR-004 | TimescaleDB for time-series telemetry storage |
| ADR-005 | confluent-kafka as the Python Kafka client |
| ADR-006 | SGP4 for satellite orbit propagation |
| ADR-007 | Eclipse-aware battery and thermal physics models |
| ADR-008 | Cylindrical shadow model for eclipse detection |
| ADR-009 | Satellite dataclass as stateful domain object |
| ADR-010 | pydantic-settings for centralised configuration |
| ADR-011 | psycopg2 (synchronous) as TimescaleDB driver for the sink |
| ADR-012 | FastAPI as the REST API framework |
| ADR-013 | Streamlit as the operator dashboard framework |
| ADR-014 | Isolation Forest for anomaly detection |
| ADR-015 | MLflow for experiment tracking |
| ADR-016 | LEO/HEO training data separation |
| ADR-017 | J2+J3+J4+J5+J6 HPOP as navigation truth reference |
| ADR-018 | Python for OTFS waveform implementation |
| ADR-019 | Azure AKS as cloud deployment platform *(Sprint 8, planned)* |
| ADR-020 | ILP ground station scheduler *(Sprint 7, complete)* |

---

## Author

**Stephen Ogodo**
Data Scientist / ML Engineer — AMDARI
PhD Researcher — Signal Waveform for Satellite Communication, Navigation,
and Remote Sensing (LEO-focused, OTFS-ISAC)

GitHub: [@stephenogodo](https://github.com/stephenogodo)

---

## Licence

MIT Licence — see [`LICENSE`](LICENSE) for details.
