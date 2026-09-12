# ILMOP — Intelligent LEO Monitoring and Operation Platform

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.5.0--beta-blue" alt="version"/>
  <img src="https://img.shields.io/badge/python-3.12%2B-brightgreen" alt="python"/>
  <img src="https://img.shields.io/badge/tests-116%20passing-brightgreen" alt="tests"/>
  <img src="https://img.shields.io/badge/sprints-5%20of%207%20complete-orange" alt="sprints"/>
  <img src="https://img.shields.io/badge/licence-MIT-lightgrey" alt="licence"/>
</p>

ILMOP is a full-stack, production-grade Mission Operations System (MOS)
for Low Earth Orbit (LEO) satellite constellations, built from first
principles in Python. It demonstrates the complete operational chain of
modern commercial satellite operations: physics-accurate spacecraft
simulation, real-time event streaming, time-series persistence, REST API
delivery, and AI-assisted anomaly detection — all running as independent,
loosely coupled microservices connected by an Apache Kafka event backbone.

The platform is simultaneously a **software engineering portfolio project**
demonstrating scalable cloud-native architecture, a **research instrument**
providing the operational ground segment for a PhD programme in OFDM
waveform design for Integrated Satellite Communication, Navigation, and
Remote Sensing (ISAC), and a **progressive scalability demonstration**
showing the same architecture handling one satellite through a
24-satellite multi-plane constellation without structural change.

---

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
│  Layer 7 — Operator Dashboard     Streamlit real-time HMI           │
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
│  Layer 2 — Ground Segment         Contact window simulation         │
│                                   ILP scheduler (Sprint 7)          │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 1 — Space Segment          SGP4 orbit propagation            │
│            (Simulator)            Eclipse-aware physics models       │
└─────────────────────────────────────────────────────────────────────┘
          ↑ telemetry flows up              ↓ commands flow down
```

### Key architectural decisions

**Event-driven over point-to-point.** All inter-service communication
flows through Kafka topics (`telemetry.{satellite_id}`,
`alarms.{satellite_id}`, `commands.{satellite_id}.{stage}`). The
simulator has no knowledge of how many consumers exist or what they do
with the data. Adding a new consumer — a payload analytics service, a
PhD navigation validator, an external integration — requires no change
to any existing service. Fan-out, replay, and per-satellite ordering are
free consequences of this design.

**Schema-first.** The `Telemetry` Pydantic model in
`shared/schemas/telemetry_schema.py` is the single data contract across
all six pipeline components. Every Kafka message is validated against it
on both production and consumption. Schema changes are versioned and
every MLflow model artifact records which schema version it was trained
against — a safeguard against silent model staleness.

**Physics before statistics.** The satellite simulator produces
eclipse-correlated telemetry: battery discharges in Earth's shadow,
solar panel power drops to zero, temperature cycles with the orbital
period. This is not cosmetic. An anomaly detection model trained on
random-walk telemetry learns nothing operationally meaningful because
there is no normal pattern to learn. The eclipse-aware physics models
are the architectural prerequisite for meaningful AI — not an
optimisation on top of it.

**Separation of operational and precision orbit models.** SGP4 (fast,
industry-standard, 100–500 m accuracy) is used throughout ILMOP for
contact scheduling, eclipse detection, ground track visualisation, and
telemetry simulation. A separate HPOP (High Precision Orbit Propagation)
model using poliastro with EGM2008 gravity and NRLMSISE-00 atmosphere
is used exclusively as the navigation truth reference for the PhD
waveform validation pipeline, providing 1–10 m accuracy sufficient to
validate sub-100-metre OFDM ranging claims against peer-review standards.

**Single data gateway.** The Streamlit dashboard is a pure API client —
it calls FastAPI exclusively and never connects to TimescaleDB directly.
Any caching, rate limiting, authentication, or business logic added to
the API layer is automatically inherited by the dashboard. This is the
architectural principle the literature calls the Backend for Frontend
(BFF) pattern.

---

## Constellation design

ILMOP implements four progressive demonstration scenarios, each
representing a distinct level of operational complexity:

### Scenario 1 — Single satellite
One satellite in an ISS-class 550 km, 51.6° inclination orbit.
Baseline for pipeline validation and the initial anomaly detection
training dataset. Eclipse fraction ~33% per 95.5-minute orbit.

### Scenario 2 — Single-plane LEO constellation (6 satellites)
Six satellites equally spaced in one orbital plane. Contact frequency
increases from one pass per ~95 minutes (Scenario 1) to one pass per
~16 minutes. With 60° satellite spacing and a 37° ground visibility
arc at 550 km, only 1–2 satellites are visible simultaneously —
insufficient for GPS-style trilateration. Scenario 2 demonstrates
Doppler-based navigation (single-pass) and sequential accumulated
ranging across multiple passes within a short observation window.
GPS-style simultaneous multi-satellite trilateration is demonstrated
in Scenario 3 where four planes distribute satellites across the sky.

### Scenario 3 — Multi-plane LEO constellation (24 satellites, 4 × 6)
Four orbital planes, 90° RAAN separation, six satellites per plane.
Global mid-latitude coverage (±53°). Multiple satellites visible
from most ground stations at any time, creating antenna scheduling
conflicts that the Sprint 7 ILP ground station scheduler resolves.
This is the primary operational scale demonstration and the training
dataset for the production LEO anomaly detection model.

### Scenario 4 — Molniya HEO polar constellation (6 satellites, standalone)
Six satellites in Molniya orbits (63.4° inclination, eccentricity 0.74,
apogee 39,750 km over the northern hemisphere). The 63.4° inclination
is the critical angle at which Earth's J2 oblateness exerts zero net
torque on the argument of perigee, keeping apogee permanently fixed
over the northern hemisphere. Six satellites spaced 60° apart in mean
anomaly provide continuous polar coverage with simultaneous dual-satellite
visibility for antenna handover demonstration from Svalbard (78°N) and
Fairbanks (65°N).

**Scenario 4 is architecturally and analytically separate from
Scenarios 1–3.** LEO and Molniya telemetry have incompatible statistical
signatures (eclipse fraction, battery cycling pattern, thermal profile,
contact window duration), and training one anomaly detection model on
both datasets produces a baseline that fits neither orbit type correctly.
Each orbit type has its own `orbit_type` tag in the Telemetry schema,
its own TimescaleDB query filter, its own MLflow experiment, and its own
production model. The scheduling paradigm is also different: LEO
operations schedule many short contact windows (5–10 minutes); Molniya
operations plan activity within one long continuous link (up to 8 hours
from a high-latitude station). (See ADR-016 for the full three-reason
argument for this separation.)

---

## Simulator physics

The satellite simulator (`services/satellite_simulator/`) implements
five physical models:

**Orbit propagation (`orbit.py`):** SGP4 via the `sgp4` Python library,
initialised from orbital elements rather than TLE strings. Produces ECI
position and velocity vectors, converted to geodetic coordinates via
Greenwich Sidereal Time rotation. Inclination, RAAN, altitude, and mean
anomaly are all configurable, enabling the four constellation scenarios
to be instantiated from a single YAML configuration file.

**Eclipse detection (`orbit.py`):** Cylindrical shadow model using the
Vallado low-precision Sun ephemeris (accurate to ~1°, sufficient for
eclipse timing within 2 minutes). Validated empirically: 33.3% eclipse
fraction over a simulated orbit against the theoretical 33–36% for a
550 km 51.6° orbit in August.

**Battery model (`battery.py`):** Eclipse-aware state-of-charge model.
Discharge rate −0.25%/tick in eclipse; charge rate +0.15%/tick in
sunlight. Solar panel output: exactly 0 W in eclipse, Gaussian
(μ=1300 W, σ=20 W) in sunlight. Terminal voltage: linear mapping of
SoC to 26.0–28.0 V range.

**Thermal model (`thermal.py`):** First-order thermal lag toward
separate eclipse (−20°C) and sunlight (+35°C) equilibrium targets,
with thermal gain α=0.008 per tick and Gaussian noise (σ=0.15°C).
Produces the characteristic ±25°C thermal swing per orbit observed
in real LEO spacecraft.

**Contact and compute models (`telemetry.py`):** Ground station contact
simulation (5–10 minute passes, 50–100 minute gaps) with CPU utilisation
trending higher during contact (active downlink processing). Replaced in
Sprint 7 by a geometry-driven ILP scheduler computing actual contact
windows from orbital mechanics and ground station coordinates.

**Telemetry continuity:** The simulator generates telemetry continuously
at 1 Hz regardless of the `in_contact` flag. The flag models the
satellite's link state — it does not gate the data flow. This mirrors
real spacecraft behaviour: onboard solid-state recorders store telemetry
across non-contact periods (~90% of the orbit) for downlink at the next
pass. All telemetry flows into Kafka and TimescaleDB continuously,
ensuring the anomaly detection model trains on the full orbital physics,
not just the ~10% of data generated during contact windows. Note: stored
telemetry latency (anomalies discoverable only at the next contact
window) is a known gap relative to production operations — documented
in the ConOps Section 16.

---

## Telemetry schema (v2.0)

Every record produced by the simulator and persisted to TimescaleDB:

| Field | Type | Description |
|-------|------|-------------|
| `satellite_id` | str | Satellite identifier (e.g. `SAT-A1`) |
| `timestamp` | datetime | UTC measurement time |
| `latitude_deg` | float | Geodetic latitude ±51.6° (LEO) |
| `longitude_deg` | float | Geodetic longitude ±180° |
| `altitude_km` | float | Altitude above Earth's surface |
| `in_eclipse` | bool | True when in Earth's shadow |
| `in_contact` | bool | True during ground station contact |
| `battery_pct` | float | Battery state of charge 0–100% |
| `battery_voltage_v` | float | Terminal voltage 26.0–28.0 V |
| `solar_panel_power_w` | float | Solar output (0 in eclipse) |
| `temperature_c` | float | Spacecraft temperature −30 to +70°C |
| `cpu_utilization_pct` | float | OBC CPU utilisation |
| `memory_utilization_pct` | float | OBC memory utilisation |
| `downlink_rate_mbps` | float | Downlink rate (0 when not in contact) |
| `uplink_rate_mbps` | float | Uplink rate (0 when not in contact) |
| `safe_mode` | bool | Spacecraft safe mode flag |
| `anomaly_flag` | bool | True when flagged by anomaly detection service |
| `orbit_type` | str | `LEO_CIRCULAR` or `HEO_MOLNIYA` |

---

## PhD research integration

ILMOP serves as the operational ground segment for a PhD research
programme on OFDM waveform design for Integrated Satellite
Communication, Navigation, and Remote Sensing (ISAC). The waveform
has already demonstrated communication capability through over-the-air
transmission, reception, and decoding of text and image signals.
Navigation and remote sensing demonstrations are in progress.

The mapping between ILMOP scenarios and PhD demonstrations:

| ILMOP scenario | PhD demonstration | Why |
|---|---|---|
| Scenario 1 | Communication | Point-to-point link baseline |
| Scenario 2 | Navigation (Doppler + sequential) | Higher contact frequency; Doppler ranging per pass and accumulated multi-pass positioning |
| Scenario 3 | Remote sensing (mid-latitude) | Multiple passes per day, coverage geometry |
| Scenario 4 | Remote sensing (polar) | Molniya apogee dwell gives extended observation over fixed polar target |

For the navigation demonstration, ILMOP's standard SGP4 model (100–500 m
accuracy) is supplemented by a HPOP `NavigationTruthModel` (1–10 m
accuracy using poliastro with EGM2008 + NRLMSISE-00) as the truth
reference. This separation is essential for peer-review credibility:
claiming sub-100-metre navigation accuracy against a 300-metre truth
reference is not defensible. The paper states this explicitly and
documents the error budget of both models.

---

## Research outputs

ILMOP supports an active research programme. Publications arising from
this work will be listed here upon acceptance.

---

## Technology stack

| Layer | Technology | Version | Decision |
|-------|-----------|---------|----------|
| Language | Python | 3.12+ | ADR-001 |
| Schema validation | Pydantic | v2.13+ | ADR-002 |
| Configuration | pydantic-settings | 2.15+ | ADR-010 |
| Event streaming | Apache Kafka (KRaft) | latest | ADR-003 |
| Kafka client | confluent-kafka | 2.15+ | ADR-005 |
| Time-series DB | TimescaleDB (PostgreSQL 16) | pg16 | ADR-004 |
| DB driver — sink | psycopg2-binary | 2.9+ | ADR-011 |
| DB driver — API | asyncpg | 0.31+ | ADR-012 |
| Orbit propagation | sgp4 | 2.27 | ADR-006 |
| Precision orbit | poliastro | Sprint 6 | ADR-017 |
| REST API | FastAPI + Uvicorn | 0.141+ | ADR-012 |
| Dashboard | Streamlit | 1.63+ | ADR-013 |
| Cache | Redis | 7-alpine | — |
| ML / tracking | scikit-learn + MLflow | 1.8 / 3.16 | ADR-014/015 |
| ILP scheduler | PuLP | Sprint 7 | ADR-019 |
| Cloud | Azure AKS + Event Hubs | Sprint 7 | ADR-018 |
| Testing | pytest | 9.1+ | — |

---

## Project status

| Sprint | Version | Title | Status | Tests |
|--------|---------|-------|--------|-------|
| 1 | v0.2.0-alpha | Dynamic Spacecraft Telemetry Simulator | ✅ Complete | 43/43 |
| 2 | v0.2.1-alpha | Simulator Physics Upgrade | ✅ Complete | 43/43 |
| 3 | v0.3.0-alpha | Streaming Telemetry Platform | ✅ Complete | 57/57 |
| 4 | v0.4.0-beta | Telemetry Data Platform — API and Dashboard | ✅ Complete | 75/75 |
| 5 | v0.5.0-beta | Intelligent Digital Twin — AI/ML | ✅ Complete | 116/116 |
| 6 | v0.6.0-beta | Demonstration Layer — HPOP, Fleet Dashboard | ⬅ Next | — |
| 7 | v1.0.0 | Cloud-Native Platform — Azure AKS | Pending | — |

Full sprint scope, risk register, ADR register, and session log:
[`docs/ILMOP_Project_Tracker.md`](docs/ILMOP_Project_Tracker.md)

---

## Sprint 5 capabilities — what is now operational

Sprint 5 added the intelligent layer on top of the Sprint 1–4 data platform.
The following are all operational:

**Four-scenario demo runner** — a single command runs any constellation scenario:
```bash
python run_demo.py --scenario 1              # 1 satellite, real time
python run_demo.py --scenario 2              # 6 satellites, single plane
python run_demo.py --scenario 3 --speed 60  # 24 satellites, 4 planes, 60× speed
python run_demo.py --scenario 4 --speed 10  # 6 Molniya HEO satellites (standalone)
```

**Fault injection** for anomaly detection model training:
```bash
python run_demo.py --scenario 1 --fault battery_degradation
python run_demo.py --scenario 1 --fault thermal_runaway
python run_demo.py --scenario 1 --fault safe_mode_trigger
```

**Anomaly detection training** (after generating data with the demo runner):
```bash
python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR
python -m services.anomaly_detection.train --orbit-type HEO_MOLNIYA
```

**Real-time anomaly detection** (scores every incoming Kafka record):
```bash
python -m services.anomaly_detection.detector
```

**Key Sprint 5 design decisions:**
- `orbit_type` field in the Telemetry schema (v2.1) enforces LEO/HEO training data
  separation — LEO and Molniya models are trained and scored independently (ADR-016)
- `fault_injected` flag labels synthetic fault records so they are excluded from
  normal training data and do not trigger operator alarms
- `time_multiplier` (`--speed`) enables training data generation at up to 3600×
  real time — one week of 24-satellite data in under 3 minutes

---

## Quickstart

### Prerequisites
- Python 3.12+
- Docker Desktop
- Git

### 1. Clone and create virtual environment

```bash
git clone https://github.com/stephenogodo/ILMOP.git
cd ILMOP
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if you need non-default values
```

### 3. Start infrastructure

```bash
docker compose --profile streaming --profile database --profile cache up -d

# Initialise TimescaleDB schema (first time only)
psql postgresql://ilmop:ilmop@localhost:5432/ilmop \
     -f services/telemetry_sink/schema.sql
```

### 4. Run the pipeline

Open four terminals in the project root:

```bash
# Terminal 1 — satellite simulator (Scenario 1: single satellite)
python run_demo.py --scenario 1

# Terminal 2 — TimescaleDB sink
python services/telemetry_sink/sink.py

# Terminal 3 — REST API
uvicorn services.api.main:app --reload

# Terminal 4 — operator dashboard
streamlit run services/dashboard/app.py
```

| Service | URL |
|---------|-----|
| Operator dashboard | http://localhost:8501 |
| REST API (interactive docs) | http://localhost:8000/docs |
| API ReDoc | http://localhost:8000/redoc |

### 5. Run the test suite

```bash
python -m pytest tests/ -v
# Expected: 75 passed, 0 warnings
```

### 6. Run a multi-satellite scenario

```bash
# 6 satellites, single plane (navigation demonstration geometry)
python run_demo.py --scenario 2

# 24 satellites, 4 planes, 60× speed (generate training data quickly)
python run_demo.py --scenario 3 --speed 60

# 6 Molniya satellites, polar coverage (standalone — separate ML model)
python run_demo.py --scenario 4 --speed 10
```

---

## Repository structure

```
ILMOP/
├── config/
│   └── constellations/          ← 4 scenario YAML files (Sprints 1–4 complete)
│       ├── scenario_1_single.yaml
│       ├── scenario_2_single_orbit.yaml
│       ├── scenario_3_multi_orbit.yaml
│       └── scenario_4_molniya_polar.yaml
├── docs/
│   ├── adr/                     ← Architecture Decision Records
│   │   ├── README.md            ← ADR index (ADR-001 to ADR-013 complete)
│   │   └── ADR-00N-*.md
│   ├── ILMOP_Project_Tracker.md ← Live sprint, risk, and ADR tracker
│   └── foundational_documents/  ← Project charter, ADD, SRS, BRD, ConOps
├── infrastructure/
│   └── docker-compose.yml       ← Kafka (KRaft), TimescaleDB, Redis
├── services/
│   ├── api/                     ← FastAPI REST layer
│   │   ├── main.py              ← App entry point, lifespan
│   │   ├── db.py                ← asyncpg pool dependency
│   │   ├── cache.py             ← Redis cache helpers
│   │   └── routers/             ← health, satellites, telemetry, alarms
│   ├── anomaly_detection/       ← Isolation Forest + MLflow ✅
│   ├── dashboard/               ← Streamlit operator dashboard
│   ├── kafka_consumer/          ← Debug consumer (pipeline verification)
│   ├── kafka_producer/          ← Telemetry producer
│   ├── predictive_health/       ← Battery SoH, thermal trend (Sprint 6)
│   ├── satellite_simulator/     ← SGP4 orbit, physics models, Satellite domain object
│   │   ├── orbit.py             ← SGP4 propagation + eclipse detection
│   │   ├── battery.py           ← Eclipse-aware battery model
│   │   ├── thermal.py           ← First-order thermal lag model
│   │   ├── satellite.py         ← Mutable state domain object
│   │   └── telemetry.py         ← TelemetryGenerator orchestrator
│   ├── scheduler/               ← ILP ground station scheduler (Sprint 7)
│   └── telemetry_sink/          ← TimescaleDB sink consumer
│       ├── sink.py              ← Batched Kafka consumer, idempotent writes
│       └── schema.sql           ← Hypertable + continuous aggregate
├── shared/
│   ├── config.py                ← pydantic-settings — all configuration
│   └── schemas/
│       ├── telemetry_schema.py  ← Telemetry Pydantic model (v2.0, 18 fields)
│       └── alarm_schema.py      ← Alarm Pydantic model v1.0 ✅
├── tests/                       ← pytest suite (75 tests, 0 warnings)
├── .env.example                 ← Environment variable template
├── .gitignore
├── LICENSE
├── requirements.txt
└── run_demo.py                  ← Single entry point for all scenarios
```

---

## Architecture Decision Records

Every significant technical decision is documented with context,
alternatives considered, rationale, and consequences. Current register:

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

Full ADR documents: [`docs/adr/`](docs/adr/)

---

## Author

**Stephen Ogodo**
Data Scientist / ML Engineer — TerraNova Resilience Analytics Ltd / NEXYGENE
PhD Researcher — Signal Waveform for Satellite Communication, Navigation,
and Remote Sensing (LEO-focused, OFDM ISAC)

GitHub: [@stephenogodo](https://github.com/stephenogodo)

---

## Licence

MIT Licence — see [`LICENSE`](LICENSE) for details.
