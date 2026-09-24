# ILMOP Architecture Design Document (ADD)

**Version:** 0.6.0-beta (Sprint 6 complete)
**Date:** September 2026
**Author:** Stephen Ogodo
**Status:** Living document — updated after each sprint

---

## 1. Purpose and scope

This document describes the software architecture of the Intelligent LEO
Monitoring and Operation Platform (ILMOP). It records architectural decisions
at the structural level — how components are organised, how they communicate,
and why major structural choices were made. Per-decision justification is in
the ADR register (docs/adr/); this document provides the integrated view.

ILMOP is both a PhD research platform and a production-oriented satellite
operations system. The architecture must satisfy two audiences simultaneously:
a thesis examiner evaluating research contribution quality, and a practitioner
evaluating operational deployability. ADR-001 records the language choice
that enables both.

---

## 2. Seven-layer architecture

ILMOP is structured in seven functional layers. Each layer depends only on
layers below it; upper layers never import from lower layers without going
through defined interfaces.

| Layer | Name | Technology | Sprint | Status |
|-------|------|-----------|--------|--------|
| 1 | Space segment simulator | SGP4 + eclipse-aware physics | 1–2 | ✅ Complete |
| 2 | Ground segment model | ContactModel (ILP scheduler Sprint 7) | 1, 7 | Partial |
| 3 | Schema and serialisation | Pydantic v2, JSON | 1 | ✅ Complete |
| 4 | Event streaming backbone | Apache Kafka (KRaft mode) | 3 | ✅ Complete |
| 5 | Time-series storage | TimescaleDB (PostgreSQL 16) | 3–4 | ✅ Complete |
| 6 | AI/ML anomaly detection | Isolation Forest + MLflow | 5 | ✅ Complete |
| 7 | Operator interface | FastAPI, Streamlit, Redis | 4 | ✅ Complete |

### Sprint 6 additions (waveform and navigation layer)

Sprint 6 added two new functional layers between Layer 6 (anomaly detection)
and Layer 7 (operator interface). These layers are specific to the OTFS-ISAC
thesis research and do not affect the operational layers below them.

| Layer | Name | Technology | Sprint | Status |
|-------|------|-----------|--------|--------|
| 6a | OTFS waveform layer | scipy, numba @njit, poliastro | 6 | ✅ Complete |
| 6b | Navigation validation | J2–J6 HPOP, sgp4, numpy | 6 | ✅ Complete |
| 7a | Fleet dashboard | Streamlit, Plotly Scattergeo | 6 | ✅ Complete |
| 7b | Coverage statistics | Streamlit, Plotly | 6 | ✅ Complete |

---

## 3. Service inventory

### 3.1 Simulation services (`services/satellite_simulator/`)

| Module | Purpose | Key dependencies |
|--------|---------|-----------------|
| `orbit.py` | SGP4 orbital propagation | sgp4, pyerfa |
| `battery.py` | Eclipse-aware battery model | numpy |
| `thermal.py` | Sinusoidal thermal physics | numpy |
| `telemetry.py` | Telemetry assembly + Kafka producer | confluent-kafka |
| `contact.py` | Contact window detection (elevation threshold) | orbit.py |

**Design note:** SGP4 accuracy (100–500 m over 24 h) is sufficient for
operational scheduling (contact windows, eclipse detection). It is not
sufficient as a navigation truth reference — see ADR-017 and Layer 6b.

### 3.2 Infrastructure services (`services/`)

| Module | Purpose | Key dependencies |
|--------|---------|-----------------|
| `telemetry_sink/sink.py` | Kafka consumer → TimescaleDB batch insert | psycopg2 |
| `api/main.py` | FastAPI REST layer (OpenAPI 3.0) | FastAPI, Redis |
| `anomaly_detection/detector.py` | Two-layer hybrid detector (limit-check + IF) | sklearn, mlflow |
| `anomaly_detection/train.py` | Isolation Forest training pipeline | sklearn, pandas, sqlalchemy |

### 3.3 OTFS waveform services (`services/otfs/`) — Sprint 6

| Module | Purpose | Key dependencies |
|--------|---------|-----------------|
| `orbital_profile.py` | OrbitalFrame time series from SGP4 | sgp4 |
| `channel_emulator.py` | FSPL + Rician + Doppler + AWGN channel model | numpy |

**Data bridge:** The orbital profile exporter is the only module that
crosses the boundary between the operational layer (SGP4 orbital mechanics)
and the waveform layer (OTFS channel model). It provides three physical
quantities per simulation frame: slant range r(t), range rate ṙ(t), and
elevation angle θ(t). The channel emulator computes FSPL, Doppler, and
Rician K-factor from these three quantities and nothing else.

### 3.4 Navigation services (`services/navigation/`) — Sprint 6

| Module | Purpose | Key dependencies |
|--------|---------|-----------------|
| `navigation_truth.py` | HPOP J2–J6 truth propagator + SP3 parser | scipy, poliastro, numba |
| `validator.py` | OTFS pseudorange vs HPOP truth comparison | navigation_truth, channel_emulator |

**Truth model accuracy:** The J2+J3+J4+J5+J6 scipy RK45 propagator achieves
~3–5 m accuracy over a single 8-minute LEO pass. poliastro 0.7.0 provides
the @njit framework; the actual force model functions are self-implemented
(ADR-017). The truth/claim ratio against the OTFS navigation accuracy
claim (~50–200 m) is approximately 10:1 — the minimum for peer-review
defensibility (Proakis and Salehi, 2008).

### 3.5 Dashboard services (`services/dashboard/`) — Sprint 4 + Sprint 6

| Module | Purpose | Key dependencies |
|--------|---------|-----------------|
| `app.py` | Streamlit operator dashboard (Sprints 4–5) | streamlit, plotly |
| `coverage_engine.py` | Pure-Python coverage statistics backend | orbital_profile |
| `pages/fleet_dashboard.py` | World map, 4 scenarios, 7 ground stations | streamlit, plotly |
| `pages/coverage_statistics.py` | Contact fraction, revisit, GDOP statistics | coverage_engine |

---

## 4. Data flows

### 4.1 Operational data flow (Sprints 1–5)

```
run_demo.py
  └─ SatelliteSimulator (orbit + battery + thermal)
       └─ KafkaProducer ──► telemetry.{sat_id} topics
                                    │
                         TelemetrySink (consumer)
                                    │
                         TimescaleDB hypertable
                                    │
                    ┌───────────────┴──────────────┐
                    │                              │
              FastAPI REST                  AnomalyDetector
                    │                              │
               Redis cache               AlarmPublisher ──► alarms table
                    │                              │
              Streamlit dashboard ◄────────────────┘
```

### 4.2 OTFS waveform + navigation data flow (Sprint 6)

```
OrbitalProfileExporter (SGP4)
  └─ OrbitalFrame[] ──► OTFSChannelEmulator
  │                          └─ ChannelFrame[] (FSPL, Rician, Doppler, AWGN)
  │
  └─ NavigationTruthModel (HPOP J2–J6)
       └─ TruthFrame[] (true pseudorange)
            │
       NavigationValidator
            └─ ValidationResult (RMS, GDOP, bias, coverage statistics)
```

### 4.3 Fleet dashboard data flow (Sprint 6)

```
OrbitalProfileExporter (SGP4) ──► [r, ṙ, θ, az] per satellite per step
        │
coverage_engine.compute_coverage_stats()
        │
        ├─► contact_fraction, n_passes, revisit_time
        ├─► mean/peak simultaneous satellites
        └─► fix_fraction (≥4 simultaneous)
                │
        Streamlit pages
        ├─ fleet_dashboard.py (world map, pass schedule)
        └─ coverage_statistics.py (metrics, timeline, bar charts)
```

---

## 5. External interfaces and dependencies

| Dependency | Version | Purpose | ADR |
|-----------|---------|---------|-----|
| sgp4 | ≥2.20 | Orbital propagation (all layers) | ADR-006 |
| poliastro | 0.7.0 | @njit J2–J6 framework for HPOP | ADR-017 |
| numba | ≥0.67 | JIT compilation for J2–J6 force functions | ADR-017 |
| scipy | ≥1.17 | RK45 numerical integration for HPOP | ADR-017 |
| astropy | ≥8.0 | UTC/TT time handling in poliastro | ADR-017 |
| Apache Kafka | 3.x (KRaft) | Event streaming | ADR-003 |
| TimescaleDB | 2.x (PostgreSQL 16) | Time-series storage | ADR-004 |
| MLflow | ≥2.0 | Experiment tracking | ADR-015 |
| Streamlit | ≥1.x | Operator and research dashboards | ADR-013 |
| Plotly | ≥7.0 | Interactive charts and world map | — |
| FastAPI | ≥0.100 | REST API layer | ADR-012 |
| Pydantic | v2 | Schema validation | ADR-002 |
| scikit-learn | ≥1.0 | Isolation Forest | ADR-014 |

**poliastro version pinning:** poliastro 0.7.0 must be used exactly.
The `CowellPropagator` class was introduced in 0.13+; 0.7.0 uses the
`cowell` function with `ad=` keyword. The J2 and J3 perturbation
functions were removed from the public API between versions. All six
zonal harmonics (J2–J6) are now self-implemented as @njit functions,
making the force model independent of poliastro's internal API.

---

## 6. Key architectural constraints

**SGP4 is operational truth, not navigation truth.** SGP4 serves the
operational layers (contact scheduling, eclipse detection, Doppler profile
for the OTFS channel emulator) where 100–500 m accuracy is acceptable. It
is never used as the navigation truth reference. The HPOP J2–J6 propagator
(~3–5 m per pass) serves that purpose exclusively (ADR-017).

**Ground station as parameter, not constant.** All Sprint 6 navigation
modules accept ground station position as a constructor parameter. The
Sprint 7 ILP scheduler can pass any selected station coordinate without
touching the navigation code. This is the decoupling that makes the ILP
handoff clean (see ADR-020, planned).

**Coverage engine separated from Streamlit.** `coverage_engine.py` contains
no Streamlit imports. The Streamlit page imports from it. This ensures the
statistics backend is fully testable without a running browser session —
the pytest suite imports only the engine, never the page.

**Molniya altitude_km is semi-major axis offset.** In OrbitalProfileExporter,
`altitude_km` is the semi-major axis offset from Earth's surface
(a = R_Earth + altitude_km), not the perigee altitude. For a Molniya orbit
with 500 km perigee and 0.74 eccentricity, the correct `altitude_km` is
20,200 (giving a = 26,571 km). Using 500 would give a nearly circular orbit
at 500 km rather than a Molniya HEO.

---

## 7. Sprint 7 planned additions

The following additions are planned for Sprint 7 (v1.0.0):

- **Azure AKS deployment** — all seven service layers deployed as Kubernetes
  pods on Azure AKS. Kafka replaced by Azure Event Hubs (Kafka-compatible wire
  protocol, zero code change). TimescaleDB hosted on Azure Database for
  PostgreSQL Flexible Server. MLflow on Azure Blob Storage backend (ADR-019).

- **ILP ground station scheduler** — replaces the contact timer with a binary
  integer programme that maximises priority-weighted contacts across multiple
  ground stations simultaneously. Active for Scenario 3 (24 satellites) and
  Scenario 4 (Molniya with Svalbard + Fairbanks dual-station) where the
  scheduling problem is genuinely combinatorial (ADR-020).

- **Four-scenario demonstration** — end-to-end `run_demo.py` progression
  through Scenarios 1→2→3→4, showing the full ILMOP capability stack.

---

## 8. References

- Fielding, R. T. (2000). Architectural Styles and the Design of Network-based
  Software Architectures. PhD thesis, UC Irvine.
- Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008). Isolation Forest. ICDM 2008.
- Montenbruck, O., & Gill, E. (2000). Satellite Orbits. Springer.
- Proakis, J. G., & Salehi, M. (2008). Digital Communications (5th ed.).
  McGraw-Hill.
- Raviteja, P., Viterbo, E., & Hong, Y. (2018). OTFS performance on static
  multipath channels. IEEE WCL, 8(3), 745–748.
- Vallado, D. A. (2013). Fundamentals of Astrodynamics (4th ed.). Microcosm.
- Zaharia, M., et al. (2018). Accelerating the Machine Learning Lifecycle
  with MLflow. VLDB Workshop.
