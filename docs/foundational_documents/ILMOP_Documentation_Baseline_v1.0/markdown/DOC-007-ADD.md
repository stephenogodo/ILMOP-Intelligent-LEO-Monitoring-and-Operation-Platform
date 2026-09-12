# ILMOP Architecture Design Document (ADD)
**Document ID:** DOC-007
**Version:** 0.5.0 (reflects Sprint 1–5 implementation)
**Status:** Active — updated each sprint
**Last updated:** 2026-08-25

---

## 1. Purpose

This document describes the architecture of the Intelligent LEO
Monitoring and Operation Platform (ILMOP): what the system is, how it
is organised, what each component does, how data flows through it, and
why it is structured the way it is.

This document describes the *what* and *how*. For the *why* behind each
significant architectural decision, see the ADR index at
`docs/adr/README.md`.

---

## 2. System overview

ILMOP is a ground software platform for monitoring and operating a Low
Earth Orbit (LEO) satellite or constellation. It ingests simulated
spacecraft telemetry, streams it through an event pipeline, persists it
to a time-series database, runs AI-based anomaly detection, and presents
the results to an operator through a real-time dashboard.

When fully implemented (Sprint 6), ILMOP will constitute a complete
**spacecraft digital twin**: a virtual model that mirrors the satellite's
physical state in real time, forecasts future states, and proposes or
executes operational responses within defined autonomous boundaries.

**Current implementation status (post Sprint 3):**

| Layer | Component | Status |
|---|---|---|
| Simulation | Satellite simulator (SGP4 + physics models) | ✅ Complete |
| Streaming | Kafka producer and consumer | ✅ Complete |
| Storage | TimescaleDB sink + schema | ✅ Complete |
| Configuration | `shared/config.py` (pydantic-settings) | ✅ Complete |
| REST API | FastAPI service | ✅ Complete |
| Dashboard | Streamlit real-time display | ✅ Complete |
| AI/ML | Anomaly detection + MLflow | Sprint 5 |
| Cloud | Azure AKS deployment | Sprint 6 |
| Scheduler | Ground station contact planner | Sprint 6 |

---

## 3. Architecture principles

**1. Schema-first.** The `Telemetry` Pydantic model in
`shared/schemas/telemetry_schema.py` is the single data contract.
Every component that reads or writes a telemetry record validates
against it. Schema changes are versioned and documented.

**2. Event-driven.** Components communicate through Kafka topics, not
direct calls. A producer publishes; consumers subscribe independently.
No component knows how many others exist or what they do with the data.

**3. Separation of mutable state and immutable records.** The `Satellite`
dataclass holds mutable simulation state. The `Telemetry` Pydantic model
is an immutable snapshot. Only `Telemetry` records cross service
boundaries (into Kafka, into TimescaleDB, into the API).

**4. Configuration without code changes.** All environment-specific
values (broker addresses, database credentials, topic names) live in
`shared/config.py` and are overrideable by environment variables.
Moving from local development to Docker to Azure requires only
environment variable changes, not code edits.

**5. Physics before statistics.** The simulator models physical
causality (battery charges in sunlight, discharges in eclipse) rather
than statistical approximation (random walk). This produces training
data with the correlation structure that anomaly detection models
require.

**6. Human-in-the-loop by default.** AI models propose; humans (or
tightly scoped automated policies) dispose. Anomaly detection flags
and recommends — it does not command autonomously until Sprint 6
defines explicit autonomous boundaries.

---

## 4. Seven-layer architecture

ILMOP's architecture is organised into seven layers, in the order
that data flows through the system:

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 7 — Operator Dashboard        Streamlit (Sprint 4)       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 6 — AI/ML Inference           Isolation Forest + MLflow  │
│                                      (Sprint 5)                  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5 — Storage                   TimescaleDB + Redis cache  │
│                                      (Sprint 3–4)                │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4 — Kafka Event Backbone      KRaft, topic-per-satellite │
│                                      (Sprint 1–3)                │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3 — Frame Ingestion           TelemetryProducer          │
│            & Serialisation           (Sprint 1–3)                │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2 — Ground Segment            ContactModel (Sprint 2–3)  │
│                                      Scheduler (Sprint 6)        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1 — Space Segment             SGP4 simulator             │
│            (Simulator)               (Sprint 1–2)                │
└─────────────────────────────────────────────────────────────────┘
        ↑ telemetry flows up          ↓ commands flow down
```

Commands flow in the opposite direction: from the dashboard through
Kafka to the ground segment and (in a real system) up to the satellite
via RF uplink.

---

## 5. Service catalogue

### 5.1 `services/satellite_simulator/`

**Role:** Generates physically realistic telemetry for a simulated LEO
satellite at a configurable tick rate (default 1 Hz).

**Key classes:**
- `OrbitModel` — SGP4 orbit propagation; returns lat, lon, alt,
  `in_eclipse` for the current UTC time
- `BatteryModel` — eclipse-aware SoC model; returns battery %, voltage,
  solar panel power
- `ThermalModel` — first-order thermal lag toward eclipse/sunlight
  equilibrium; returns temperature
- `_ContactModel` — state machine simulating ground station contact
  windows (5–10 min passes, 50–100 min gaps)
- `_ComputeModel` — CPU/memory random walk with contact-period spike
- `Satellite` — mutable dataclass holding the satellite's current state
- `TelemetryGenerator` — orchestrates all models; returns immutable
  `Telemetry` snapshot each tick

**Outputs:** `Telemetry` objects (via `TelemetryGenerator.generate()`)

---

### 5.2 `services/kafka_producer/`

**Role:** Reads telemetry from `TelemetryGenerator`, serialises to JSON,
publishes to `telemetry.{satellite_id}` Kafka topic.

**Key design decisions:**
- Message key = `satellite_id` (guarantees per-satellite ordering within
  a partition)
- Topic name from `settings.telemetry_topic(satellite_id)`
- Producer acknowledgement mode: `acks=all` (wait for broker
  confirmation before continuing)
- Structured logging; clean flush on shutdown

---

### 5.3 `services/kafka_consumer/`

**Role:** General-purpose debug consumer. Subscribes to
`telemetry.{satellite_id}`, validates each message through the
`Telemetry` schema, and logs the result. Not a production sink —
used for pipeline verification.

---

### 5.4 `services/telemetry_sink/`

**Role:** Production persistence consumer. Consumes validated telemetry
records from Kafka and writes them to TimescaleDB in batches.

**Key design decisions:**
- Manual Kafka offset commit: committed only after successful database
  write, ensuring no data loss on crash
- `INSERT … ON CONFLICT DO NOTHING`: idempotent writes tolerate
  at-least-once Kafka delivery without duplicate rows
- `psycopg2.extras.execute_batch()`: one database round-trip per batch
- Batch parameters: 50 records or 5 seconds, whichever comes first

---

### 5.5 `shared/schemas/`

**Role:** Data contracts shared across all services.

- `telemetry_schema.py` — `Telemetry` Pydantic model (v2.0): 17 fields
  covering orbital state, power, thermal, compute, communications,
  and status flags. The `schema_version` field in TimescaleDB ties
  every stored row to the schema it was written from.

---

### 5.6 `shared/config.py`

**Role:** Centralised configuration. Single `Settings` instance imported
by every service. All values overrideable by environment variables.

**Defined settings:**
- Kafka: bootstrap servers, topic prefixes, consumer group IDs
- TimescaleDB: host, port, name, user, password, computed URL/DSN
- Simulator: satellite ID, tick interval
- API (Sprint 4): host, port
- Dashboard (Sprint 4): port

---

---

### 5.6 `services/api/`

**Role:** REST API gateway — the single data access layer for all
upstream consumers.  No component except the API connects to
TimescaleDB directly.

**Application server:** Uvicorn (ASGI, async-first)
**Framework:** FastAPI 0.141+

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check + DB connectivity |
| `GET` | `/satellites` | Active satellite list with latest status |
| `GET` | `/telemetry/{sat_id}/latest` | Most recent record (Redis-cached, 5 s TTL) |
| `GET` | `/telemetry/{sat_id}` | Time-range query (`from`, `to`, `limit`) |
| `GET` | `/telemetry/{sat_id}/summary` | 5-min aggregated buckets from continuous aggregate |

**Key components:**
- `main.py` — FastAPI app, lifespan (pool + Redis), CORS middleware
- `db.py` — `get_pool()` dependency returning the asyncpg pool
- `cache.py` — Optional Redis client; graceful fallback if Redis unavailable
- `routers/` — One module per resource (`health`, `satellites`, `telemetry`)

**Interactive docs:** available at `/docs` (Swagger UI) and `/redoc` when running.

---

### 5.7 `services/dashboard/`

**Role:** Real-time operator dashboard.  A pure API client — calls the
FastAPI exclusively; never connects to TimescaleDB or Kafka directly.

**Framework:** Streamlit 1.63+

**Panels:**
- Live status row: 6 metrics (battery %, temperature, solar power, CPU, eclipse, contact)
- Orbital position: lat, lon, alt + world map marker (`st.map()`)
- Trending charts: battery SoC, temperature, solar power, CPU (from `/summary` endpoint)
- Alarm panel: placeholder, populated in Sprint 5

**Auto-refresh:** `time.sleep(N)` → `st.rerun()` — configurable 1–10 s interval via sidebar.

**Start command:** `streamlit run services/dashboard/app.py`

---

### 5.8 `services/satellite_simulator/constellation.py`

**Role:** Multi-satellite fleet runner. Loads a constellation scenario
from a YAML config file in `config/constellations/`, instantiates one
`TelemetryGenerator` per satellite, and generates all telemetry snapshots
each tick.

**Key classes:**
- `ConstellationConfig` — loaded from YAML; expands orbital plane
  definitions into individual `SatelliteConfig` objects with evenly
  spaced mean anomalies
- `ConstellationManager` — owns all `TelemetryGenerator` instances;
  `generate_all()` returns a dict of `{satellite_id: Telemetry}` per tick;
  supports `time_multiplier` and `fault_type` parameters

**Orbit types supported:** `LEO_CIRCULAR` (Scenarios 1–3) and
`HEO_MOLNIYA` (Scenario 4). The `orbit_type` is read from the YAML
and propagated to every `TelemetryGenerator` and every `Telemetry`
record — gates ML model routing per ADR-016.

---

### 5.9 `run_demo.py`

**Role:** Single entry point for all four constellation scenarios.
Instantiates `ConstellationManager`, publishes to Kafka, and logs
progress.

**Usage:**
```
python run_demo.py --scenario 1              # 1 satellite, real time
python run_demo.py --scenario 3 --speed 60  # 24 satellites, 60× speed
python run_demo.py --scenario 1 --fault battery_degradation
```

**`--speed` parameter:** advances simulated time at `N × wall-clock rate`.
At `--speed 3600`, one week of 24-satellite training data generates in
approximately 168 seconds. The contact model and orbit model both receive
the simulated time step so all physics scale correctly at any speed.

---

### 5.10 `services/anomaly_detection/`

**Role:** Real-time anomaly detection. Three modules:

**`features.py`** — extracts a 10-element feature vector from each
`Telemetry` record. Includes interaction terms (`battery_pct ×
solar_panel_power_w`, `temperature_c × in_eclipse_int`) that capture
the eclipse-battery-thermal correlations that make the problem learnable.
Orbit type is a routing criterion, not a feature (see ADR-016).

**`train.py`** — trains an Isolation Forest model for a given `orbit_type`.
Exports training data from TimescaleDB with `WHERE orbit_type = %s AND
fault_injected = FALSE`. Logs all parameters, metrics, and the model
artifact to MLflow with `schema_version`, `orbit_type`, and `scenario`
tags. Registers the model as `ilmop-anomaly-{orbit_type}` in the MLflow
model registry.

**`detector.py`** — Kafka consumer that scores every incoming telemetry
record in real time. `ModelRouter` loads and caches the correct Isolation
Forest model per `orbit_type`. Severity thresholds: score < −0.05 =
WARNING, score < −0.15 = CRITICAL. Publishes `Alarm` records to
`alarms.{satellite_id}`. Fault-injected records are skipped — they are
anomalous by design and should not trigger operator alarms.


## 6. Kafka topic structure

Topic naming convention: `{domain}.{satellite_id}`

| Topic | Producer | Consumers | Content |
|---|---|---|---|
| `telemetry.{sat_id}` | `TelemetryProducer` | Sink, anomaly detector, dashboard | `Telemetry` JSON record |
| `alarms.{sat_id}` | Anomaly detection service (Sprint 5) | Dashboard, notification service | `Alarm` JSON record |
| `commands.{sat_id}.requested` | Operator dashboard (Sprint 4) | Validation service | Proposed command |
| `commands.{sat_id}.approved` | Validation service | Uplink service | Approved command |
| `commands.{sat_id}.uplinked` | Uplink service | Dashboard, audit log | Confirmation |

**Partitioning:** all topics are partitioned by `satellite_id` as the
message key. This guarantees per-satellite ordering and ensures that
all records for a given satellite are processed by the same consumer
partition member.

**Consumer groups:**

| Group ID | Service | Purpose |
|---|---|---|
| `ilmop-sink` | `TelemetrySink` | Persists every record to TimescaleDB |
| `ilmop-anomaly-detection` | Anomaly service (Sprint 5) | Scores every record |
| `ilmop-dashboard` | Dashboard consumer (Sprint 4) | Live display feed |

Each group receives every record independently — Kafka fan-out with
no coupling between consumers.

---

## 7. Data model

### 7.1 Telemetry record (Pydantic schema v2.0)

```
satellite_id           str       Satellite identifier (e.g. "SAT-001")
timestamp              datetime  UTC timestamp of the measurement
latitude_deg           float     Geodetic latitude  [-51.6°, +51.6°]
longitude_deg          float     Geodetic longitude [-180°, +180°]
altitude_km            float     Altitude above Earth's surface [~530–570 km]
in_eclipse             bool      True when satellite is in Earth's shadow
in_contact             bool      True during a ground station contact window
battery_pct            float     Battery state of charge [0–100 %]
battery_voltage_v      float     Terminal voltage [26.0–28.0 V]
solar_panel_power_w    float     Solar panel output [0 W eclipse / ~1300 W sun]
temperature_c          float     Spacecraft temperature [−30 to +70 °C]
cpu_utilization_pct    float     OBC CPU utilisation [5–95 %]
memory_utilization_pct float     OBC memory utilisation [20–85 %]
downlink_rate_mbps     float     Downlink data rate [0 or ~120 Mbps]
uplink_rate_mbps       float     Uplink command rate [0 or ~20 Mbps]
safe_mode              bool      True if spacecraft is in safe mode
anomaly_flag           bool      True if flagged by anomaly detection
```

### 7.2 TimescaleDB schema

The `telemetry` table is a TimescaleDB hypertable partitioned on the
`time` column. Every column in the Pydantic schema has a corresponding
column in the database, plus `schema_version TEXT` to track which
schema version produced the row.

**Indexes:**
- `(satellite_id, time DESC)` — primary dashboard access pattern
- `(in_eclipse, time DESC) WHERE in_eclipse = TRUE` — eclipse analysis
- `(in_contact, time DESC) WHERE in_contact = TRUE` — contact analysis

**Continuous aggregate:** `telemetry_5min` pre-computes per-satellite
5-minute bucket averages, refreshed automatically every 5 minutes.
Used by Sprint 4 dashboard trending charts.

---

## 8. End-to-end data flow

One complete tick of the simulation, from physics model to database row:

```
1. TelemetryGenerator.generate() called (every 1 second)
   │
   ├─ OrbitModel.propagate()       → lat, lon, alt, in_eclipse
   ├─ _ContactModel.update()       → in_contact, dl_rate, ul_rate
   ├─ BatteryModel.update(eclipse) → battery_pct, voltage, solar_w
   ├─ ThermalModel.update(eclipse) → temperature_c
   └─ _ComputeModel.update(contact)→ cpu_pct, memory_pct
       │
       ↓
2. Satellite state updated (mutable Satellite dataclass)
       │
       ↓
3. Telemetry snapshot created (immutable Pydantic model, validated)
       │
       ↓
4. TelemetryProducer serialises to JSON
   → publishes to Kafka topic: telemetry.SAT-001
   → message key: "SAT-001"
       │
       ↓ (Kafka fan-out — all consumers receive independently)
       │
   ┌───┴──────────────┬────────────────────┐
   ↓                  ↓                    ↓
5a. TelemetrySink    5b. Anomaly detector  5c. Dashboard consumer
    validates             (Sprint 5)            (Sprint 4)
    executes INSERT       scores record         updates live chart
    to TimescaleDB        publishes to
    commits Kafka         alarms.SAT-001
    offset
```

---

## 9. Deployment topology

### Development (current — Sprints 1–5)

```
Developer machine (Windows)
├── Python venv (.venv-1)
│   ├── satellite simulator  (runs as a script)
│   ├── kafka producer       (runs as a script)
│   ├── kafka consumer       (runs as a script)
│   └── telemetry sink       (runs as a script)
└── Docker Compose
    ├── kafka         (apache/kafka:latest, KRaft mode, port 9092)
    └── timescaledb   (timescale/timescaledb:latest-pg16, port 5432)
```

Services are started with Docker Compose profiles:
```bash
docker compose --profile streaming up -d   # starts Kafka
docker compose --profile database  up -d   # starts TimescaleDB
```

### Production (Sprint 6 target — Azure AKS)

```
Azure Kubernetes Service (AKS)
├── Namespace: ilmop
│   ├── Deployment: satellite-simulator
│   ├── Deployment: telemetry-producer
│   ├── Deployment: telemetry-sink
│   ├── Deployment: anomaly-detection
│   ├── Deployment: api (FastAPI)
│   └── Deployment: dashboard (Streamlit)
├── Azure Event Hubs (Kafka-compatible API)
│   └── Event Hub namespace: ilmop-events
└── Azure Database for PostgreSQL (Flexible Server)
    └── TimescaleDB extension enabled
```

The application code requires no changes for the Sprint 6 migration —
only the environment variables in `shared/config.py` change (Kafka
bootstrap server, database connection string).

---

## 10. Technology stack

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| Language | Python | 3.12+ | ADR-001 |
| Schema validation | Pydantic | v2.13+ | ADR-002 |
| Configuration | pydantic-settings | 2.15+ | ADR-010 |
| Event streaming | Apache Kafka (KRaft) | latest | ADR-003 |
| Kafka client | confluent-kafka | 2.15+ | ADR-005 |
| Time-series DB | TimescaleDB | pg16 | ADR-004 |
| DB driver (sink) | psycopg2-binary | 2.9+ | ADR-011 |
| DB driver (API) | asyncpg | Sprint 4 | — |
| Orbit propagation | sgp4 | 2.27 | ADR-006 |
| Testing | pytest | 9.1+ | — |
| REST API | FastAPI + Uvicorn | 0.141+ | ADR-012 |
| Dashboard | Streamlit | 1.63+ | ADR-013 |
| ML / tracking | scikit-learn + MLflow | Sprint 5 | — |
| Container | Docker + Compose | — | — |
| Cloud | Azure AKS + Event Hubs | Sprint 6 | — |

---

## 11. Security considerations (current state)

Security hardening is post-Sprint 6. Current known gaps:

- TimescaleDB credentials are plaintext defaults (`ilmop/ilmop`) —
  acceptable for local development; must be replaced with secrets
  management (Azure Key Vault) for Sprint 6
- Kafka has no authentication or TLS — acceptable for local development;
  Azure Event Hubs enforces TLS and SASL/PLAIN authentication
- No role-based access control on the API (Sprint 4 must include at
  minimum API key authentication)
- The `.env` file (if created) must be in `.gitignore` — never committed

---

## 12. ADR reference index

All architectural decisions are documented in `docs/adr/`:

| ADR | Decision |
|---|---|
| ADR-001 | Python as primary language |
| ADR-002 | Pydantic v2 for schema validation |
| ADR-003 | Apache Kafka as event streaming backbone |
| ADR-004 | TimescaleDB for time-series storage |
| ADR-005 | confluent-kafka Python client |
| ADR-006 | SGP4 for orbit propagation |
| ADR-007 | Eclipse-aware battery and thermal physics |
| ADR-008 | Cylindrical shadow model for eclipse detection |
| ADR-009 | Satellite dataclass as stateful domain object |
| ADR-010 | pydantic-settings for configuration management |
| ADR-011 | psycopg2 for TimescaleDB sink driver |
