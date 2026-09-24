# ILMOP — Intelligent LEO Monitoring and Operation Platform

<p align="center">
  <img src="https://img.shields.io/badge/version-v0.6.0--beta-blue" alt="version"/>
  <img src="https://img.shields.io/badge/python-3.14%2B-brightgreen" alt="python"/>
  <img src="https://img.shields.io/badge/tests-281%20passing-brightgreen" alt="tests"/>
  <img src="https://img.shields.io/badge/sprints-6%20of%207%20complete-orange" alt="sprints"/>
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
providing the operational ground segment for a PhD programme in OTFS-ISAC
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
model using J2+J3+J4+J5+J6 zonal harmonic numerical integration
(scipy RK45, poliastro @njit acceleration, ~3–5 m accuracy per pass)
is used exclusively as the navigation truth reference for the PhD
waveform validation pipeline. The truth/claim ratio of ~10:1 against
the OTFS pseudorange accuracy claim (~50–200 m) satisfies peer-review
defensibility standards. (See ADR-017.)

**Single data gateway.** The Streamlit dashboard is a pure API client —
it calls FastAPI exclusively and never connects to TimescaleDB directly.
Any caching, rate limiting, authentication, or business logic added to
the API layer is automatically inherited by the dashboard. This is the
architectural principle the literature calls the Backend for Frontend
(BFF) pattern.

**Coverage engine separated from Streamlit.** `services/dashboard/coverage_engine.py`
contains no Streamlit imports. The statistics page imports from it.
This ensures the coverage backend is fully testable without a running
browser session — the pytest suite imports only the engine, never the page.

---

## Constellation design

ILMOP implements four progressive demonstration scenarios, each
representing a distinct level of operational complexity:

### Scenario 1 — Single satellite
One satellite in an ISS-class 550 km, 51.6° inclination orbit.
Baseline for pipeline validation and the initial anomaly detection
training dataset. Eclipse fraction ~33% per 95.5-minute orbit.
Contact fraction from Cambridge: ~6.6% of a 2-hour observation window.

### Scenario 2 — Single-plane LEO constellation (6 satellites)
Six satellites equally spaced in one orbital plane. Contact frequency
increases from one pass per ~95 minutes (Scenario 1) to one pass per
~16 minutes. Contact fraction from Cambridge: ~45.5%. With 60° satellite
spacing and a 37° ground visibility arc at 550 km, only 1–2 satellites
are visible simultaneously — insufficient for GPS-style trilateration.
Scenario 2 demonstrates OTFS Doppler-based navigation (single-pass) and
sequential accumulated ranging across multiple passes within a short
observation window.

### Scenario 3 — Multi-plane LEO constellation (24 satellites, 4 × 6)
Four orbital planes, 90° RAAN separation, six satellites per plane.
Global mid-latitude coverage (±53°). Contact fraction from Cambridge:
~51.2%. Mean simultaneous satellites: 0.64. Multiple satellites visible
from most ground stations at any time, creating antenna scheduling
conflicts that the Sprint 7 ILP ground station scheduler resolves.

### Scenario 4 — Molniya HEO polar constellation (6 satellites, standalone)
Six satellites in Molniya orbits (63.4° inclination, eccentricity 0.74,
apogee ~39,750 km over the northern hemisphere). The 63.4° inclination
is the critical angle at which Earth's J2 oblateness exerts zero net
torque on the argument of perigee, keeping apogee permanently fixed
over the northern hemisphere. Six satellites spaced 60° apart in mean
anomaly provide continuous polar coverage with simultaneous dual-satellite
visibility for antenna handover demonstration from Svalbard (78.2°N) and
Fairbanks (64.8°N).

**Scenario 4 is architecturally and analytically separate from
Scenarios 1–3.** LEO and Molniya telemetry have incompatible statistical
signatures (eclipse fraction, battery cycling pattern, thermal profile,
contact window duration), and training one anomaly detection model on
both datasets produces a baseline that fits neither orbit type correctly.
Each orbit type has its own `orbit_type` tag in the Telemetry schema,
its own TimescaleDB query filter, its own MLflow experiment, and its own
production model. (See ADR-016 for the full three-reason argument for
this separation.)

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
550 km 51.6° orbit.

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

---

## Telemetry schema (v2.1)

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
programme on OTFS (Orthogonal Time Frequency Space) modulation as a
unified waveform for Integrated Sensing and Communication (ISAC) in LEO
satellite systems. The three thesis demonstrations — communication,
navigation, and remote sensing — will all be conducted using the same
OTFS-ISAC waveform under the physically grounded LEO satellite channel
model implemented in Sprint 6.

The mapping between ILMOP scenarios and PhD demonstrations:

| ILMOP scenario | PhD demonstration | Why |
|---|---|---|
| Scenario 1 | Communication | Point-to-point link baseline |
| Scenario 2 | Navigation (OTFS Doppler + sequential ranging) | Higher contact frequency; Doppler ranging per pass and accumulated multi-pass positioning |
| Scenario 3 | Remote sensing (mid-latitude) | Multiple passes per day, coverage geometry |
| Scenario 4 | Remote sensing (polar) | Molniya apogee dwell gives extended observation over fixed polar target |

**Sprint 6 navigation validation results (simulation):**

The OTFS navigation validation pipeline compares pseudorange estimates
against a high-precision J2+J3+J4+J5+J6 HPOP truth reference (~3–5 m
accuracy per pass). Initial simulation results:

| Ground station | Latitude | Max elevation | RMS pseudorange | GDOP min |
|---|---|---|---|---|
| Lagos, Nigeria (upper bound) | 6.5°N | 87.4° | 8.87 m | 1.001 |
| Cambridge, UK (deployment) | 52.2°N | 59.0° | 9.99 m | 1.167 |
| Theoretical upper bound | — | 90° | 6.16 m | 1.000 |

The 28% Cambridge vs Lagos gap is a geodetic constraint — Cambridge lies
0.6° above the orbital inclination, limiting maximum elevation to ~82.4°.
The formula is: θ_max = arctan{[cos(φ−i) − R_E/(R_E+h)] / sin(φ−i)}.
This is not a waveform limitation; it is a ground station geometry effect.

---

## Research outputs

ILMOP supports an active research programme. Publications arising from
this work will be listed here upon acceptance.

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
| World map | Plotly | 7.0+ | — |
| Cache | Redis | 7-alpine | — |
| ML / tracking | scikit-learn + MLflow | 1.8 / 3.16 | ADR-014/015 |
| ILP scheduler | PuLP | Sprint 7 | ADR-020 |
| Cloud | Azure AKS + Event Hubs | Sprint 7 | ADR-019 |
| Testing | pytest | 9.1+ | — |

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
| 7 | v1.0.0 | Cloud-Native Platform — Azure AKS | ⬅ Next | — |

Full sprint scope, risk register, ADR register, and session log:
[`docs/ILMOP_Project_Tracker.md`](docs/ILMOP_Project_Tracker.md)

---

## Simplifying assumptions

ILMOP is a research and demonstration platform, not a production flight
system. The following simplifying assumptions are applied throughout.

**Orbital mechanics**
- SGP4 orbit propagation with fixed orbital elements — no manoeuvre
  modelling, no epoch decay, no J5+ gravitational harmonics (in the
  operational layer), no ocean tides, and no relativistic corrections.
  Positional accuracy is 100–500 m for LEO circular orbits.
- Eclipse detection uses a cylindrical shadow model with a sharp step
  transition — no penumbra (partial shadowing at eclipse entry/exit).
- The HPOP navigation truth model uses J2+J3+J4+J5+J6 zonal harmonic
  numerical integration (scipy RK45, ~3–5 m per pass). Atmospheric drag
  (NRLMSISE-00) and lunisolar perturbations are not included — their
  combined contribution over an 8-minute pass is < 1 m, negligible
  relative to the OTFS ranging noise floor.

**Spacecraft physics**
- Single-node thermal model — one temperature value for the entire
  spacecraft.
- Idealised battery — fixed charge/discharge rates; no capacity fade.
- Constant solar panel output in sunlight — no panel degradation,
  no solar incidence angle variation.
- No attitude control modelling.
- No radiation environment modelling.

**Ground segment**
- Binary contact model — perfect link assumed for the full pass duration.
- Single ground station approximation in Sprints 1–5 — geometry-driven
  scheduling begins in Sprint 7.
- Telemetry flows continuously in the simulation — stored telemetry
  latency not modelled.

**Anomaly detection**
- Isolation Forest trained on stationary, mode-agnostic telemetry —
  no concept drift detection; periodic retraining required.
- Synthetic fault injection uses linear degradation rather than
  physically modelled failure mechanisms.

**Navigation demonstration**
- Single-frequency pseudorange ranging — ionospheric delay is not
  eliminated; tropospheric delay and hardware biases are not modelled.
- Code-phase (pseudorange) accuracy, not carrier-phase — expected
  accuracy is metres to tens of metres, not centimetres.

The full assumptions register with rationale is in
[`docs/markdown/DOC-003_Concept_of_Operations_ConOps.md`](docs/markdown/DOC-003_Concept_of_Operations_ConOps.md).

---

## Sprint 6 capabilities — what is now operational

Sprint 6 added the OTFS waveform layer and navigation validation pipeline
on top of the Sprint 1–5 operational platform.

**OTFS channel emulator validation** (no services required):
```bash
python validate_channel_emulator.py
# Expected: ALL 8/8 PHYSICS CHECKS PASSED
# FSPL: 156.19 dB at 59° | Doppler: ±51.9 kHz | SNR: −6.7 to −15.8 dB
```

**Navigation validation pipeline**:
```bash
python validate_navigation_validator.py
# Expected: ALL 9/9 CHECKS PASSED
# RMS: 9.99 m (Cambridge) | Bias: 0.12 m | GDOP: 1.17–5.68 | truth=hpop
```

**Fleet dashboard** (world map, all 4 scenarios, 7 ground stations):
```bash
streamlit run services/dashboard/pages/fleet_dashboard.py
```

**Coverage statistics** (contact fraction, revisit time, simultaneous sats):
```bash
streamlit run services/dashboard/pages/coverage_statistics.py
```

**Key Sprint 6 design decisions:**
- `OrbitalFrame` dataclass (range, range rate, elevation, azimuth) is
  the sole data bridge between the orbital mechanics layer and the OTFS
  waveform layer — the channel emulator has no direct dependency on SGP4
- `coverage_engine.py` is separated from the Streamlit page so the
  statistics backend is fully testable without a browser session
- poliastro 0.7.0 API: `cowell` is a function with `ad=` kwarg, not a
  `CowellPropagator` class (introduced in 0.13+); J2–J6 force functions
  are self-implemented as `@njit` for version independence (ADR-017)
- Molniya `altitude_km = 20200` is the semi-major axis offset from
  Earth's surface (a = 26,571 km), not the perigee altitude (537 km)

---

## Sprint 5 capabilities — what is also operational

**Four-scenario demo runner**:
```bash
python run_demo.py --scenario 1              # 1 satellite, real time
python run_demo.py --scenario 2              # 6 satellites, single plane
python run_demo.py --scenario 3 --speed 60  # 24 satellites, 4 planes
python run_demo.py --scenario 4 --speed 10  # 6 Molniya HEO (standalone)
```

**Fault injection** for anomaly detection training:
```bash
python run_demo.py --scenario 1 --fault battery_degradation
python run_demo.py --scenario 1 --fault thermal_runaway
python run_demo.py --scenario 1 --fault safe_mode_trigger
```

**Anomaly detection training and inference**:
```bash
python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR
python -m services.anomaly_detection.detector
```

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
pip install poliastro astropy numba plotly   # Sprint 6 dependencies
```

### 2. Start infrastructure

```bash
docker compose --profile streaming --profile database --profile cache up -d
```

### 3. Run the operational pipeline

Open four terminals in the project root:

```bash
# Terminal 1 — satellite simulator
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
| REST API docs | http://localhost:8000/docs |
| Fleet dashboard | `streamlit run services/dashboard/pages/fleet_dashboard.py` |
| Coverage statistics | `streamlit run services/dashboard/pages/coverage_statistics.py` |

### 4. Run the test suite

```bash
python -m pytest tests/ -v
# Expected: 281 passed, 1 skipped
```

---

## Repository structure

```
ILMOP/
├── config/
│   └── constellations/          ← 4 scenario YAML files
├── docs/
│   ├── adr/                     ← Architecture Decision Records (ADR-001 to ADR-018)
│   ├── ILMOP_Project_Tracker.md ← Sprint, risk, and ADR tracker
│   └── ILMOP_Architecture_Design_Document.md
├── infrastructure/
│   └── docker-compose.yml       ← Kafka (KRaft), TimescaleDB, Redis
├── services/
│   ├── api/                     ← FastAPI REST layer
│   ├── anomaly_detection/       ← Isolation Forest + MLflow
│   ├── dashboard/
│   │   ├── app.py               ← Streamlit operator dashboard (Sprints 4–5)
│   │   ├── coverage_engine.py   ← Coverage statistics backend (Sprint 6)
│   │   └── pages/
│   │       ├── fleet_dashboard.py      ← World map, 4 scenarios, 7 stations
│   │       └── coverage_statistics.py ← Contact fraction, revisit, GDOP
│   ├── navigation/              ← Sprint 6 — navigation validation
│   │   ├── navigation_truth.py  ← HPOP J2–J6 truth model (ADR-017)
│   │   └── validator.py         ← OTFS pseudorange validation pipeline
│   ├── otfs/                    ← Sprint 6 — OTFS waveform layer
│   │   ├── orbital_profile.py   ← OrbitalFrame exporter (SGP4 → channel)
│   │   └── channel_emulator.py  ← FSPL + Rician + Doppler + AWGN
│   ├── satellite_simulator/     ← SGP4 orbit, physics models
│   └── telemetry_sink/          ← TimescaleDB sink consumer
├── shared/
│   ├── config.py
│   └── schemas/
│       ├── telemetry_schema.py  ← Telemetry Pydantic model (v2.1, 18 fields)
│       └── alarm_schema.py
├── tests/                       ← pytest suite (281 tests, 1 skipped)
├── validate_channel_emulator.py ← Sprint 6 — 8/8 physics checks
├── validate_navigation_validator.py ← Sprint 6 — 9/9 physics checks
├── run_demo.py                  ← Single entry point for all scenarios
└── requirements.txt
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
| ADR-014 | Isolation Forest for anomaly detection |
| ADR-015 | MLflow for experiment tracking |
| ADR-016 | LEO/HEO training data separation |
| ADR-017 | J2+J3+J4+J5+J6 HPOP as navigation truth reference |
| ADR-018 | Python for OTFS waveform implementation |
| ADR-019 | Azure AKS as cloud deployment platform *(Sprint 7, planned)* |
| ADR-020 | ILP ground station scheduler *(Sprint 7, planned)* |

Full ADR documents: [`docs/adr/`](docs/adr/)

---

## Author

**Stephen Ogodo**
Data Scientist / ML Engineer — TerraNova Resilience Analytics Ltd / NEXYGENE
PhD Researcher — Signal Waveform for Satellite Communication, Navigation,
and Remote Sensing (LEO-focused, OTFS-ISAC)

GitHub: [@stephenogodo](https://github.com/stephenogodo)

---

## Licence

MIT Licence — see [`LICENSE`](LICENSE) for details.
