# ILMOP Project Tracker

**Project:** Intelligent LEO Monitoring and Operation Platform
**GitHub:** github.com/stephenogodo/ILMOP-Intelligent-LEO-Monitoring-and-Operation-Platform
**Current version:** v0.7.0-beta (Sprint 7 in progress)
**Last updated:** October 2026
**Total tests passing:** 515/515 (1 skipped)

---

## Sprint status

| Sprint | Version | Title | Status | Tests |
|--------|---------|-------|--------|-------|
| 1 | v0.2.0-alpha | Dynamic Spacecraft Telemetry Simulator | ✅ Complete | 43/43 |
| 2 | v0.2.1-alpha | Simulator Physics Upgrade (SGP4, eclipse) | ✅ Complete | 43/43 |
| 3 | v0.3.0-alpha | Streaming Telemetry Platform (Kafka, TimescaleDB) | ✅ Complete | 57/57 |
| 4 | v0.4.0-beta | Telemetry Data Platform (FastAPI, Streamlit, Redis) | ✅ Complete | 75/75 |
| 5 | v0.5.0-beta | Intelligent Digital Twin (AI/ML anomaly detection) | ✅ Complete | 152/152 |
| 6 | v0.6.0-beta | Demonstration Layer (OTFS waveform, navigation, fleet dashboard) | ✅ Complete | 281/281 |
| 7 | v1.0.0 | LEO Constellation Operations Suite | 🔄 In Progress | 234/234 (so far) |

---

## Sprint 1 — Completed deliverables

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | SGP4 orbit model | `services/satellite_simulator/orbit.py` | Vallado et al. (2006) implementation via Rhodes sgp4 library |
| 2 | Battery physics model | `services/satellite_simulator/battery.py` | Eclipse-aware charge/discharge |
| 3 | Thermal physics model | `services/satellite_simulator/thermal.py` | Rician-like sinusoidal swing |
| 4 | Telemetry Pydantic schema | `shared/schemas/telemetry.py` | Typed, JSON-serialisable |
| 5 | Scenario configuration YAML | `config/scenarios/` | 4 constellation scenarios |
| 6 | Sprint 1 tests | `tests/orbit_test.py`, `tests/battery_test.py`, `tests/thermal_test.py` | 43 tests |

---

## Sprint 2 — Completed deliverables

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | Eclipse-aware battery model | `services/satellite_simulator/battery.py` | Cylindrical shadow model; Montenbruck & Gill (2000) Alg 29 |
| 2 | TEMP_MAX_C raised to 120°C | `services/satellite_simulator/thermal.py` | Fault was stabilising at 72°C due to cap conflict |
| 3 | State persistence | `data/sim_state/{sat_id}.json` | Battery/thermal state survives process restart |
| 4 | Fault injection (`--fault`) | `run_demo.py` | battery_degradation, thermal_runaway, panel_degradation |
| 5 | `--speed` multiplier | `run_demo.py` | Default 10×; timestamps remain realistic |
| 6 | ThermalModel internal state sync | `services/satellite_simulator/telemetry.py` | Fault sync back into model |

---

## Sprint 3 — Completed deliverables

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | Apache Kafka (KRaft mode) | `infrastructure/docker-compose.yml` | No ZooKeeper; wildcard topic subscription |
| 2 | Telemetry sink | `services/telemetry_sink/sink.py` | Batch insert to TimescaleDB |
| 3 | TimescaleDB hypertable | `infrastructure/init.sql` | 1-hour chunk interval; compression policy |
| 4 | Kafka producer in simulator | `services/satellite_simulator/telemetry.py` | One topic per satellite |
| 5 | `shared/config.py` | `shared/config.py` | pydantic-settings; environment variables |
| 6 | Multi-satellite scenarios | `config/scenarios/scenario_3.yaml` | 24-satellite 4-plane Walker delta |

---

## Sprint 4 — Completed deliverables

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | FastAPI REST layer | `services/api/main.py` | OpenAPI 3.0 at /docs; Fielding (2000) REST |
| 2 | Streamlit operator dashboard | `services/dashboard/app.py` | 6-second refresh default; `st.empty()` alarm panel |
| 3 | Redis cache | `infrastructure/docker-compose.yml` | Response caching for API |
| 4 | Alarm schema | `shared/schemas/alarm.py` | CRITICAL/WARNING severity; Pydantic v2 |
| 5 | Anomaly detector stub | `services/anomaly_detection/detector.py` | Placeholder for Sprint 5 |
| 6 | Timestamp fix | `services/dashboard/app.py` | KeyError 'time'→'timestamp' |

---

## Sprint 5 — Completed deliverables

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | Isolation Forest model | `services/anomaly_detection/train.py` | Liu et al. (2008, 2012); contamination=0.01 |
| 2 | Hybrid detector (Layer 1 + Layer 2) | `services/anomaly_detection/detector.py` | Layer 1: limit-check; Layer 2: IF score |
| 3 | MLflow experiment tracking | `services/anomaly_detection/` | Zaharia et al. (2018); file store backend |
| 4 | Standing alarm suppression | `services/anomaly_detection/detector.py` | 60 sim-second suppression window |
| 5 | NaN sentinel fix | `services/anomaly_detection/detector.py` | -1.0 sentinel for limit-check alarms |
| 6 | Training SQL health filter | `services/anomaly_detection/train.py` | battery_pct>5, temp<50 |
| 7 | Dynamic log in run_demo.py | `run_demo.py` | Shows batt+temp regardless of fault type |
| 8 | ADR-001 to ADR-016 citations | `docs/adr/` | Academic citations retrofitted to all 16 ADRs |
| 9 | ADR-018 | `docs/adr/ADR-018-python-for-otfs-waveform-implementation.md` | Python over MATLAB for OTFS layer |

**Sprint 5 documentation:**
- Supervisor Briefing v3.0 (corrected OTFS-ISAC demonstration status and channel model)
- Training Manual v1.8
- CV updated (Azure and CI/CD removed; PhD bullet corrected)

---

## Sprint 6 — Completed deliverables

| # | Deliverable | File | Tests |
|---|---|---|---|
| 1 | Orbital profile exporter | `services/otfs/orbital_profile.py` | 18/18 |
| 2 | OTFS channel emulator | `services/otfs/channel_emulator.py` | 29/29 |
| 3 | Navigation truth model (HPOP) | `services/navigation/navigation_truth.py` | 26/26 |
| 4 | Navigation validation pipeline | `services/navigation/validator.py` | 34/34 |
| 5 | Fleet dashboard | `services/dashboard/pages/fleet_dashboard.py` | 19/19 |
| 6 | Coverage statistics | `services/dashboard/coverage_engine.py` + `pages/coverage_statistics.py` | 26/26 |
| 7 | ADR-017 | `docs/adr/ADR-017-hpop-navigation-truth-model.md` | — |

**Validation scripts:**
- `validate_channel_emulator.py` — 8/8 physics checks
- `validate_navigation_validator.py` — 9/9 physics checks

**Sprint 6 validated results:**

| Metric | Result |
|---|---|
| Channel emulator FSPL at zenith | 156.19 dB (theory 155.1 dB ✓) |
| Channel emulator Doppler (59° pass) | ±51.9 kHz (theory ±52 kHz ✓) |
| Navigation RMS — Cambridge | 9.992 m |
| Navigation RMS — Lagos (upper bound) | 8.866 m (87.4° max elevation) |
| Navigation theoretical upper bound | 6.16 m (finite SNR) / 4.33 m (quantisation floor) |
| Navigation bias | 0.123 m (Cambridge), −1.481 m (Lagos) |
| GDOP at max elevation (Cambridge 59°) | 1.167 = 1/sin(59°) ✓ |
| Coverage S1 → S3 improvement | 6.6% → 51.2% contact fraction (7.8×) |
| Truth source | HPOP (J2+J3+J4+J5+J6, ~3–5 m accuracy) |

**Sprint 6 documentation:**
- Supervisor Briefing v4.0
- Training Manual v1.9
- ADR-017 (HPOP Navigation Truth Model)
- Navigation validation reports (theoretical UB / Lagos UB / Cambridge deployment case)

---

## Sprint 7 — LEO Constellation Operations Suite (In Progress)

**Version:** v1.0.0  
**Architecture:** Seven-layer build sequence

| Layer | Title | File | Tests | Status |
|-------|-------|------|-------|--------|
| 1 | Contact Window Geometry | `services/scheduler/geometry.py` + `contact_window.py` | 13/13 | ✅ Complete |
| 2 | ILP Ground Station Scheduler | `services/scheduler/ilp_scheduler.py` | 16/16 | ✅ Complete |
| 3 | ISL Topology Manager | `services/scheduler/isl_topology.py` | 18/18 | ✅ Complete |
| 4 | Ground Station Handover | `services/scheduler/handover.py` | 34/34 | ✅ Complete |
| 5 | Dark Satellite Detection | `services/scheduler/dark_satellite.py` | 24/24 | ✅ Complete |
| 6 | Relay Coordinator | `services/scheduler/relay_coordinator.py` | — | 🔄 Pending |
| 7 | Composite ILP Objective + Dashboard | `services/scheduler/scheduler.py` + dashboard integration | — | 🔄 Pending |

**Layer details:**

**Layer 1 — Contact Window Geometry** (13 tests)
- `geometry.py`: `GroundStation` dataclass (name, lat_deg, lon_deg, alt_km); `elevation_angle()` and `compute_contact_windows()` using spherical geometry
- `contact_window.py`: `ContactWindow` dataclass (satellite_id, station_name, aos_unix, los_unix, max_elevation_deg); `merge_overlapping_windows()`
- Tests: `tests/test_geometry.py`, `tests/test_contact_window.py`

**Layer 2 — ILP Ground Station Scheduler** (16 tests)
- `ilp_scheduler.py`: PuLP 2.9.0 ILP formulation; binary decision variables x[sat][win]; maximises total contact duration; enforces single-station constraint; 5-second solver time limit with greedy fallback
- Tests: `tests/test_ilp_scheduler.py`

**Layer 3 — ISL Topology Manager** (18 tests)
- `isl_topology.py`: `ISLLink` dataclass; `compute_isl_topology()` checks line of sight (LOS) between pairs — two satellites can communicate over an ISL only if there is LOS between them (i.e., the link path does not pass through the Earth); `build_adjacency_matrix()`; `find_relay_path()` using BFS shortest-hop routing
- Tests: `tests/test_isl_topology.py`

**Layer 4 — Ground Station Handover** (34 tests)
- `handover.py`: `HandoverState` FSM (IDLE → ACQUIRING → ACTIVE → HANDING_OVER → RELEASED); `HandoverEvent` and `HandoverPlan` dataclasses; `build_handover_plan()` with configurable `overlap_margin_s` (default 30 s); `get_active_station()` point-in-time query; `build_constellation_handover_plans()`
- Tests: `tests/test_handover.py`

**Layer 5 — Dark Satellite Detection** (24 tests)
- `dark_satellite.py`: `DarkInterval` dataclass (satellite_id, start_unix, end_unix, duration_s); `is_dark()` horizon-based check (default 600 s); `find_dark_satellites()` fleet scan; `compute_dark_intervals()` with window merge and gap detection
- Tests: `tests/test_dark_satellite.py`

**Sprint 7 test count progression:**

| Batch | Layers | New tests | Cumulative |
|-------|--------|-----------|------------|
| Batch 1 | Layer 1 | 13 | 294 |
| Batch 2 | Layer 2 | 16 | 310 |
| Batch 3 | Layer 3 | 18 | 328 |
| Batch 4 | Layer 4 | 34 | 362 |
| Batch 5 | Layer 5 | 24 | 386 |

**Pending Sprint 7 deliverables:**

| # | Deliverable | File | Notes |
|---|---|---|---|
| 6 | Relay Coordinator | `services/scheduler/relay_coordinator.py` | Uses ISL paths from Layer 3 to route data via relay satellites |
| 7 | Composite ILP Objective | `services/scheduler/scheduler.py` | α=0.5×duration + β=0.3×relay_value + γ=0.2×data_age |
| 8 | Dashboard Integration | `services/dashboard/` | Sprint 7 metrics in operator view |
| 9 | Molniya Scenario 4 | `run_demo.py` | 6 HEO satellites, Svalbard + Fairbanks, 8-hour apogee dwell |
| 10 | Azure AKS manifests | `infrastructure/k8s/` | Moved to Sprint 8 |
| 11 | ADR-019, ADR-020 | `docs/adr/` | Azure platform + ILP scheduler rationale |

**Key architectural note (ILP vs contact timer):**
ILP adds value only when there are scheduling choices to make — multiple satellites
competing for multiple ground stations simultaneously. For Scenarios 1–2, the contact
timer is correct (one satellite, trivial scheduling). ILP earns its complexity in
Sprint 7 with Scenario 3 (24 sats) and Scenario 4 (Molniya dual-station: Svalbard +
Fairbanks). See ADR-020.

---

## Risk register

| ID | Risk | Likelihood | Impact | Status | Mitigation |
|---|---|---|---|---|---|
| R1 | ~~Docker Compose YAML bug blocks Sprint 3~~ | ~~High~~ | ~~High~~ | ✅ Closed | Fixed in Sprint 3 |
| R2 | ~~Simulator has no fast-forward mode~~ | ~~High~~ | ~~High~~ | ✅ Closed | `--speed` flag added Sprint 5 |
| R3 | ~~Kafka producer has no key — multi-satellite ordering breaks~~ | ~~High~~ | ~~Medium~~ | ✅ Closed | Fixed in Sprint 3 |
| R4 | ~~Isolation Forest contamination=0.05 causes false alarms~~ | ~~High~~ | ~~High~~ | ✅ Closed | contamination=0.01 Sprint 5 |
| R5 | ~~Anomaly detection trained on nominal data only~~ | ~~High~~ | ~~High~~ | ✅ Closed | Fault injection + two-layer detector Sprint 5 |
| R6 | Azure cost overrun | Low | Medium | Open | Azure AKS deferred to Sprint 8; size for dev/test |
| R7 | ~~`shared/config.py` absent; Docker networking failures~~ | ~~High~~ | ~~Medium~~ | ✅ Closed | Created Sprint 3 |
| R8 | poliastro API version incompatibility | Medium | High | ✅ Closed | 0.7.0 API fixed Sprint 6; cowell() + ad= pattern documented |
| R9 | SGP4 truth accuracy insufficient for navigation papers | High | High | ✅ Closed | HPOP J2–J6 truth model built Sprint 6 (ADR-017) |
| R10 | ~~LEO/HEO training data mixed~~ | ~~High~~ | ~~High~~ | ✅ Closed | orbit_type filter + separate models ADR-016 |
| R11 | OTFS guard region contamination under timing error | Medium | High | Open | Adaptive guard region with safety margin δ — Contribution 2 |
| R12 | 3D position fix not achieved from Cambridge | Low | Medium | Open | Cambridge 0.6° above inclination; use Lagos or lower-latitude station |
| R13 | ILP solver infeasible for large Scenario 3 windows | Low | Medium | Open | Time-limit the solver; fall back to greedy if no solution in 5s |

---

## ADR register

| ADR | Decision | Sprint | Status |
|---|---|---|---|
| ADR-001 | Python as primary development language | 1 | ✅ Accepted |
| ADR-002 | Pydantic v2 for schema validation | 1 | ✅ Accepted |
| ADR-003 | Apache Kafka event streaming backbone | 3 | ✅ Accepted |
| ADR-004 | TimescaleDB time-series storage | 3 | ✅ Accepted |
| ADR-005 | confluent-kafka Python client | 3 | ✅ Accepted |
| ADR-006 | SGP4 orbit propagation | 2 | ✅ Accepted |
| ADR-007 | Eclipse-aware physics models | 2 | ✅ Accepted |
| ADR-008 | Cylindrical shadow eclipse model | 2 | ✅ Accepted |
| ADR-009 | Satellite state as Pydantic dataclass | 1 | ✅ Accepted |
| ADR-010 | pydantic-settings configuration | 1 | ✅ Accepted |
| ADR-011 | psycopg2 database driver | 3 | ✅ Accepted |
| ADR-012 | FastAPI REST layer | 4 | ✅ Accepted |
| ADR-013 | Streamlit operator dashboard | 4 | ✅ Accepted |
| ADR-014 | Isolation Forest anomaly detection | 5 | ✅ Accepted |
| ADR-015 | MLflow experiment tracking | 5 | ✅ Accepted |
| ADR-016 | LEO/HEO training separation | 5 | ✅ Accepted |
| ADR-017 | HPOP navigation truth model (J2–J6) | 6 | ✅ Accepted |
| ADR-018 | Python for OTFS waveform implementation | 5 | ✅ Accepted |
| ADR-019 | Azure as cloud deployment platform | 7 | Planned |
| ADR-020 | ILP ground station scheduler | 7 | Planned |

All ADRs 001–018 include academic citations.

---

## Key commands reference

```powershell
# Environment
$env:COMPOSE_FILE = "C:\Users\Admin\Documents\Projects\ILMOP\infrastructure\docker-compose.yml"
$env:MLFLOW_ALLOW_FILE_STORE = "true"

# Start infrastructure
docker compose --profile streaming --profile database --profile cache up -d

# Sprint 5 — fault injection demonstration
Remove-Item -Force data\sim_state\SAT-A1.json -ErrorAction SilentlyContinue
docker exec -i ilmop-timescaledb psql -U ilmop -d ilmop -c "TRUNCATE TABLE alarms;"
python run_demo.py --scenario 1 --fault battery_degradation --speed 10

# Sprint 6 — channel emulator validation
python validate_channel_emulator.py

# Sprint 6 — navigation validation
python validate_navigation_validator.py

# Sprint 6 — fleet dashboard
streamlit run services/dashboard/pages/fleet_dashboard.py

# Sprint 6 — coverage statistics
streamlit run services/dashboard/pages/coverage_statistics.py

# Full test suite
python -m pytest tests/ -v

# Sprint 7 — scheduler layer tests
python -m pytest tests/test_geometry.py tests/test_contact_window.py tests/test_ilp_scheduler.py tests/test_isl_topology.py tests/test_handover.py tests/test_dark_satellite.py -v

# Retrain model
python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR
```

---

## Document versions

| Document | Version | Date | Location |
|---|---|---|---|
| Supervisor Briefing | v4.0 | Sep 2026 | ILMOP_Supervisor_Briefing_v4.docx |
| Training Manual | v1.9 | Sep 2026 | ILMOP_Training_Manual_v1.9.docx |
| Build Journal | Sprint 7 | Oct 2026 | ILMOP_Sprint7_Build_Journal.docx |
| Concept of Operations | v1.5 | Sep 2026 | docs/ConOps/ |
| Architecture Design Document | v0.6.0 | Sep 2026 | docs/ADD/ |
| Navigation Validation Report | v2 (final) | Sep 2026 | Report2v2_Navigation_Three_Level_Final.docx |
| Channel Emulator Validation Report | v1 | Sep 2026 | Report1_Channel_Emulator_Validation.docx |
