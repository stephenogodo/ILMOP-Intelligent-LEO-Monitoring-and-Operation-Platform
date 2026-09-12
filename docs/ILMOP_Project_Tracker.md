# ILMOP — Live Project Tracker

**Project:** Intelligent LEO Monitoring and Operation Platform
**Current version:** v0.4.0-beta (post Sprint 4)
**Last updated:** 2026-09-08
**Tracking:** update this file at the close of every session

---

## Sprint status

| Sprint | Version | Title | Status | Tests |
|--------|---------|-------|--------|-------|
| 1 | v0.2.0-alpha | Dynamic Spacecraft Telemetry Simulator | ✅ Complete | 4 → 43 |
| 2 | v0.2.1-alpha | Simulator Physics Upgrade | ✅ Complete | 43/43 |
| 3 | v0.3.0-alpha | Streaming Telemetry Platform | ✅ Complete | 57/57 |
| 4 | v0.4.0-beta | Telemetry Data Platform — API and Dashboard | ✅ Complete | 75/75 |
| 5 | v0.5.0-beta | Intelligent Digital Twin — AI/ML | ✅ Complete | 116/116 |
| 6 | v0.6.0-beta | Demonstration Layer — Fleet Dashboard, HPOP, Coverage, Four Scenarios | ⬅ Next | — |
| 7 | v1.0.0 | Cloud-Native Platform — Azure AKS, ILP Scheduler, Molniya Standalone | Pending | — |

---

## Constellation architecture (agreed design)

Four progressive demonstration scenarios — each runnable independently
via `python run_demo.py --scenario N --speed M`.

| Scenario | Orbit type | Planes | Satellites/plane | Total | Primary purpose |
|----------|-----------|--------|-----------------|-------|-----------------|
| 1 | LEO circular | 1 | 1 | 1 | Pipeline baseline, anomaly detection training |
| 2 | LEO circular | 1 | 6 | 6 | Navigation demonstration, contact frequency |
| 3 | LEO circular | 4 | 6 | 24 | Operational scale, multi-plane scheduling |
| 4 | HEO Molniya | 1 | 6 | 6 | Polar coverage, standalone — never mixed with LEO data |

**Scenarios 1–3** share LEO_CIRCULAR orbit type and a common ML training dataset.
**Scenario 4** is a fully separate experiment with its own HEO_MOLNIYA ML model,
its own training dataset, and its own ground station configuration (Svalbard + Fairbanks).

**Molniya parameters:** inclination 63.4° (critical angle — zero apsidal precession),
eccentricity 0.74, argument of perigee 270° (apogee fixed over northern hemisphere),
perigee altitude 500 km, apogee altitude 39,750 km, period ~12 hours.
6 satellites spaced 60° apart in mean anomaly provide continuous polar coverage
with simultaneous dual-satellite visibility for antenna handover demonstration.

---

## Sprint 3 — Completed deliverables

| Deliverable | File | Notes |
|---|---|---|
| Docker Compose fix | `infrastructure/docker-compose.yml` | Fixed nested `environment:` bug, CRLF endings, `ilmov→ilmop` typo, added Kafka healthcheck, top-level `volumes:` block |
| Central config | `shared/config.py` | pydantic-settings; all env vars; `telemetry_topic()` and `alarms_topic()` helpers |
| Kafka producer rewrite | `services/kafka_producer/producer.py` | `satellite_id` as message key, topic from config, structured logging |
| Kafka consumer rewrite | `services/kafka_consumer/consumer.py` | Pydantic schema validation, structured logging, clean error handling |
| TimescaleDB sink | `services/telemetry_sink/sink.py` | Batched writes, idempotent INSERT, manual Kafka offset commit |
| TimescaleDB schema | `services/telemetry_sink/schema.sql` | Hypertable, 3 indexes, 5-min continuous aggregate |
| Config tests | `tests/config_test.py` | 7 tests including env-var override |
| Sink unit tests | `tests/sink_test.py` | 7 tests for `_parse()` — no live Kafka/DB required |
| New dependencies | `requirements.txt` | `psycopg2-binary==2.9.12`, `pydantic-settings==2.15.0` |

---

## Sprint 4 — Completed deliverables

| Deliverable | File | Notes |
|---|---|---|
| FastAPI application | `services/api/main.py` | Lifespan, asyncpg pool, CORS, router registration |
| DB layer | `services/api/db.py` | `get_pool()` dependency — asyncpg pool injection |
| Redis cache | `services/api/cache.py` | Optional Redis client; graceful fallback if unavailable |
| Health router | `services/api/routers/health.py` | `GET /health` — liveness + DB connectivity |
| Satellites router | `services/api/routers/satellites.py` | `GET /satellites` — active fleet list |
| Telemetry router | `services/api/routers/telemetry.py` | `GET /telemetry/{sat}/latest`, history, 5-min summary |
| Streamlit dashboard | `services/dashboard/app.py` | Live metrics, ground track map, trending charts, auto-refresh |
| Redis service | `infrastructure/docker-compose.yml` | `redis:7-alpine`, cache profile |
| API tests | `tests/api_test.py` | 18 tests — all endpoints, 404 cases, field validation |
| New dependencies | `requirements.txt` | fastapi, uvicorn, asyncpg, streamlit, redis, httpx2 |

---

## Sprint 5 — Completed deliverables

| Deliverable | File | Notes |
|---|---|---|
| Telemetry schema v2.1 | `shared/schemas/telemetry_schema.py` | `orbit_type` + `fault_injected` fields; backward compatible |
| TimescaleDB schema v2.1 | `services/telemetry_sink/schema.sql` | New columns, orbit_type index, alarms hypertable, retention + compression policies |
| Sink updated | `services/telemetry_sink/sink.py` | Writes new fields; schema_version 2.1 |
| Alarm schema v1.0 | `shared/schemas/alarm_schema.py` | 14-field Pydantic Alarm model |
| TelemetryGenerator v2 | `services/satellite_simulator/telemetry.py` | orbit_type, time_multiplier, fault injection, simulated time cursor |
| Fault injector | `services/satellite_simulator/telemetry.py` | BATTERY_DEGRADATION, THERMAL_RUNAWAY, SAFE_MODE_TRIGGER |
| Constellation YAML files | `config/constellations/scenario_1–4.yaml` | All four scenarios fully defined |
| ConstellationManager | `services/satellite_simulator/constellation.py` | Loads YAML, instantiates N generators, generate_all() |
| Demo runner | `run_demo.py` | --scenario 1–4 --speed N --fault type |
| Feature engineering | `services/anomaly_detection/features.py` | 10 eclipse-correlated features |
| MLflow training pipeline | `services/anomaly_detection/train.py` | Isolation Forest + MLflow; separate experiment per orbit_type |
| Anomaly detection service | `services/anomaly_detection/detector.py` | Model router by orbit_type, Kafka consumer, alarm publisher |
| Alarms API endpoint | `services/api/routers/alarms.py` | GET /alarms/{sat_id} with severity filter |
| Dashboard alarm panel | `services/dashboard/app.py` | Live alarm display, severity colour-coding |
| Config update | `shared/config.py` | mlflow_tracking_uri added |
| Tests | `tests/constellation_test.py`, `tests/alarm_schema_test.py`, `tests/anomaly_test.py` | 41 new tests; 116/116 total |

---

## Sprint 6 — Scope (Pending)

**Goal:** A live, compelling, multi-scenario demonstration of ILMOP
from one satellite to 24; sub-100-metre navigation truth reference
replacing SGP4 for the PhD navigation validation pipeline; fleet
dashboard showing all satellites simultaneously on a world map.

| # | Deliverable | File(s) | Notes |
|---|---|---|---|
| 1 | NavigationTruthModel | `services/satellite_simulator/navigation_truth.py` | poliastro HPOP with EGM2008 gravity + NRLMSISE-00 drag + lunisolar perturbations; 1–10 m accuracy vs SGP4's 100–500 m |
| 2 | SP3 ephemeris parser | `services/satellite_simulator/sp3_parser.py` | Parse IGS SP3 precise ephemeris files; 10–50 m accuracy; faster alternative to full HPOP |
| 3 | Navigation validation pipeline | `services/navigation/validator.py` | Compares OFDM ranging solution against NavigationTruthModel; computes residuals, RMS, GDOP |
| 4 | Fleet dashboard page | `services/dashboard/app.py` | All satellites plotted simultaneously on world map; colour-coded by orbit type and health status |
| 5 | Coverage statistics | `services/dashboard/pages/coverage.py` | Contact fraction, revisit time, simultaneous visibility per scenario; Scenario 1→3 comparison table |
| 6 | Four-scenario demonstration | `run_demo.py` (complete) | End-to-end verified: Scenario 1→2→3→4 each runnable cleanly; dashboard updates correctly for each |
| 7 | Scenario transition test | `tests/scenario_test.py` | Verify ConstellationManager correctly instantiates all four configurations |
| 8 | ADR-017 | `docs/adr/ADR-017-hpop-navigation-truth-model.md` | Orbit model accuracy hierarchy: SGP4 (operations) vs HPOP/SP3 (navigation truth); why the separation matters for PhD peer-review credibility |
| 9 | ADD + tracker update | — | After Sprint 6 completion |

**Key architectural principle for Sprint 6:**
SGP4 is retained as the operational orbit model throughout ILMOP.
`NavigationTruthModel` (HPOP) is used exclusively in the navigation
validation pipeline. The paper states this explicitly: "SGP4 is used
for operational orbit determination and contact scheduling.
HPOP is used as the truth reference for navigation accuracy validation,
providing approximately X-metre reference accuracy against which the
OFDM ranging solution's Y-metre residuals are evaluated."

---

## Sprint 7 — Scope (Pending)

**Goal:** Full cloud-native deployment on Azure; ILP ground station
scheduler replacing the ContactModel timer; Molniya Scenario 4
standalone demonstration from end to end.

| # | Deliverable | File(s) | Notes |
|---|---|---|---|
| 1 | Azure AKS deployment | `infrastructure/k8s/` | Helm charts for all 6–8 services; autoscaling node pools |
| 2 | Azure Event Hubs migration | `shared/config.py` | Bootstrap server → Event Hubs endpoint; zero application code changes |
| 3 | Azure PostgreSQL migration | `shared/config.py` | Connection string → Azure Database for PostgreSQL + TimescaleDB extension |
| 4 | Azure Container Registry | `infrastructure/acr/` | Push all Docker images; AKS pulls from ACR |
| 5 | Azure Key Vault | `infrastructure/keyvault/` | Secrets (DB password, Event Hubs connection string) out of environment variables |
| 6 | ILP ground station scheduler | `services/scheduler/scheduler.py` | PuLP-based ILP; computes contact windows from SGP4 + ground station positions; resolves antenna conflicts; publishes to `passes.schedule` Kafka topic |
| 7 | Ground station config | `config/ground_stations.yaml` | At minimum: Svalbard (78°N), Fairbanks (65°N), Maspalomas (28°N), Santiago (33°S), Perth (32°S) |
| 8 | Molniya Scenario 4 standalone | `run_demo.py --scenario 4` | Full end-to-end: 6 Molniya satellites, Svalbard + Fairbanks ground stations, long-dwell contact windows, HEO anomaly detection model, polar coverage map |
| 9 | Per-orbit-type model registry | `services/anomaly_detection/` | MLflow model registry with separate LEO and HEO production models; routing confirmed working for all four scenarios |
| 10 | ADR-018 | `docs/adr/ADR-018-azure-cloud-platform.md` | Why Azure over AWS/GCP: Event Hubs Kafka compatibility (zero code change), native TimescaleDB on PostgreSQL Flexible Server |
| 11 | ADR-019 | `docs/adr/ADR-019-ilp-ground-station-scheduler.md` | Why ILP (PuLP) over greedy heuristics and metaheuristics for 24-satellite / 5-station problem |
| 12 | ADD + tracker final update | — | Final version — project complete |

**Estimated Azure monthly cost (Tier 2 dev/test):** ~$152/month on-demand;
~$26/month if stopped when not in use (4 hours/day active).
Covered by Azure free credits ($200) for the first month.
Use Azure for Students ($100/year) for ongoing development.

---

## Open risks

| ID | Risk | Likelihood | Impact | Status | Mitigation |
|----|------|-----------|--------|--------|------------|
| R1 | ~~Docker Compose YAML bug blocks Sprint 3~~ | ~~High~~ | ~~High~~ | ✅ Closed | Fixed in Sprint 3 |
| R2 | ~~Simulator has no fast-forward mode~~ | ~~High~~ | ~~High~~ | ✅ Closed | `--speed` flag added in Sprint 5 |
| R3 | ~~Kafka producer has no key — multi-satellite ordering breaks~~ | ~~High~~ | ~~Medium~~ | ✅ Closed | Fixed in Sprint 3 |
| R4 | Schema v2 breaks legacy CSV export | Low | Low | **Open** | Alpha stage — acceptable; note in CHANGELOG |
| R5 | ~~No fault injection — model trained on nominal data only~~ | ~~High~~ | ~~High~~ | ✅ Closed | Fault injection implemented in Sprint 5 |
| R6 | MLflow schema version coupling not enforced | Medium | High | **Open** | Tag schema_version, orbit_type, scenario in every MLflow run from day one |
| R7 | ~~`shared/config.py` absent; Docker networking failures silent~~ | ~~High~~ | ~~Medium~~ | ✅ Closed | Created in Sprint 3 |
| R8 | Markdown documentation stubs remain empty | Medium | Medium | **Partially closed** | ADD and ADRs populated; 14 other stubs still pending |
| R9 | Azure cost overrun in Sprint 7 | Low | Medium | **Open** | Size AKS for dev/test; stop compute when not in use; use free credits first |
| R10 | Redis cache adds operational complexity | Low | Low | **Open** | Use Redis only for latest-record cache; graceful fallback already implemented |
| R11 | ~~LEO and HEO training data mixed~~ | ~~High~~ | ~~High~~ | ✅ Closed | orbit_type field enforced in schema v2.1 + TimescaleDB query filter |
| R12 | SGP4 reference accuracy (100–500 m) insufficient to validate sub-100-metre navigation claims | High | High | **Open** | Build NavigationTruthModel (poliastro HPOP, 1–10 m accuracy) in Sprint 6 before navigation paper is written |
| R13 | Molniya near-perigee SGP4 error (km-level) contaminates positioning demonstration | Medium | High | **Open** | Navigation demonstration restricted to Scenario 2 LEO only; Molniya used for remote sensing only |

---

## ADR register

| ADR | Decision | Sprint | Status |
|-----|----------|--------|--------|
| ADR-001 | Python as primary language | 1 | Accepted |
| ADR-002 | Pydantic v2 for schema validation | 1 | Accepted |
| ADR-003 | Apache Kafka as event streaming backbone | 1 | Accepted |
| ADR-004 | TimescaleDB for time-series storage | 1 | Accepted |
| ADR-005 | confluent-kafka Python client | 1 | Accepted |
| ADR-006 | SGP4 for orbit propagation | 2 | Accepted |
| ADR-007 | Eclipse-aware battery and thermal physics | 2 | Accepted |
| ADR-008 | Cylindrical shadow model for eclipse detection | 2 | Accepted |
| ADR-009 | Satellite dataclass as stateful domain object | 2 | Accepted |
| ADR-010 | pydantic-settings for configuration management | 3 | Accepted |
| ADR-011 | psycopg2 for TimescaleDB sink driver | 3 | Accepted |
| ADR-012 | FastAPI as REST API framework | 4 | Accepted |
| ADR-013 | Streamlit as operator dashboard | 4 | Accepted |
| ADR-014 | Isolation Forest for anomaly detection | 5 | Accepted |
| ADR-015 | MLflow for experiment tracking | 5 | Accepted |
| ADR-016 | LEO/HEO training data separation | 5 | Accepted |
| ADR-017 | HPOP NavigationTruthModel vs SGP4 for navigation validation | 6 | Planned |
| ADR-018 | Azure as cloud deployment platform | 7 | Planned |
| ADR-019 | ILP (PuLP) for ground station scheduler | 7 | Planned |

---

## Publication pipeline (emerging from project + PhD work)


## Document register

| Doc ID | Title | Location | Status |
|--------|-------|----------|--------|
| DOC-007 | Architecture Design Document (ADD) | `docs/.../markdown/DOC-007-ADD.md` | ✅ Active — v0.4.0 |
| ADR index | All ADRs (ADR-001 to ADR-013 complete) | `docs/adr/` | ✅ Active |
| Others (DOC-001 to DOC-015 excl. DOC-007) | Various | `docs/.../markdown/` | Stubs — populate each sprint |

---

## Session log

| Date | Sprint | What was done |
|------|--------|---------------|
| 2026-08-25 | 1–2 | Initial codebase analysis; Sprint 2 physics upgrade (SGP4, eclipse-aware models); 43 tests |
| 2026-08-25 | 2 | ADR-001 through ADR-009 written |
| 2026-08-25 | 3 | Sprint 3 complete: Docker fix, config, producer/consumer rewrite, TimescaleDB sink, 57 tests |
| 2026-08-25 | 3 | ADR-010, ADR-011, ADD (DOC-007), project tracker created |
| 2026-08-25 | 4 | Sprint 4 complete: FastAPI, Streamlit, Redis, 75 tests; ADR-012, ADR-013, ADD updated |
| 2026-09-08 | — | Brainstorming: constellation architecture (4 scenarios), Molniya HEO design, LEO/HEO ML separation, HPOP navigation truth, publication pipeline, sprint renumbering (5.5→6, 6→7) |
| 2026-09-11 | 5 | Sprint 5 complete: schema v2.1, ConstellationManager, 4 scenario YAMLs, run_demo.py, Isolation Forest, MLflow, fault injection, alarms API, dashboard alarm panel, 116/116 tests |

---

## Next session opening checklist

Before writing any code, confirm:
- [ ] Upload latest `ILMOP.zip` to sandbox
- [ ] Run `python -m pytest tests/ -q` — all 75 tests must pass
- [ ] State which sprint and task we are starting
- [ ] Confirm `--speed` flag is first Sprint 5 task before any model training begins
