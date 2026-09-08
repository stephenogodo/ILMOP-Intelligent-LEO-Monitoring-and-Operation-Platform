# ADR-001: Python as primary implementation language

**Status:** Accepted
**Sprint:** 1
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

ILMOP requires a language capable of supporting four distinct technical domains simultaneously: 
numerical simulation (orbit propagation, physics models), 
data engineering (Kafka producers and consumers, database I/O), 
machine learning (anomaly detection, predictive health models), and 
web services (REST API, real-time dashboard). 
The language choice affects every sprint from 1 to 6 and constrains which libraries, deployment patterns, and team knowledge apply.

---

## Decision

Python 3.12+ is the primary implementation language for all ILMOP services: the satellite simulator, Kafka producer and consumer, telemetry sink, FastAPI REST layer, Streamlit dashboard, and MLflow-tracked ML models.

---

## Alternatives considered

### C++
The language of choice for flight software and high-performance ground system components (YAMCS uses Java; many heritage systems use C++).
Provides deterministic memory management, sub-millisecond latency, and direct hardware access.

**Rejected because:** ILMOP is a ground software platform, not flight software. Its performance bottleneck is network I/O and database writes,
not computation. C++ offers no advantage for Kafka consumers or TimescaleDB queries, and its development velocity for a single-developer research platform is far lower. The machine learning ecosystem (scikit-learn,
TensorFlow, PyTorch) is Python-first; a C++ ML implementation would require maintaining custom inference code rather than leveraging a world-class open-source ecosystem.

### Java
The language of YAMCS, the most widely deployed open-source mission
operations system. Strong typing, JVM performance, mature Kafka client (Apache Kafka itself is written in Java/Scala). Industry precedent exists for production-grade MOS implementations in Java.

**Rejected because:** Java's development overhead (verbose syntax, compilation cycle, dependency management with Maven/Gradle) reduces iteration speed for a single developer. The Python ML ecosystem has no
Java equivalent of comparable maturity. FastAPI and Streamlit — the planned Sprint 4 web layer — are Python-native with no Java counterparts of similar productivity. The JVM's startup time also complicates
containerised microservice patterns.

### MATLAB / GNU Octave
MATLAB is widely used in aerospace engineering for signal processing, orbit analysis, and simulation. The lead developer's PhD research
(OFDM waveform design) uses MATLAB for waveform prototyping.

**Rejected because:** MATLAB's licensing cost is prohibitive for an open-source platform. GNU Octave lacks the production deployment infrastructure (Kafka clients, REST frameworks, container support) needed
for a multi-service architecture. Neither can serve as a backend for a production API or dashboard. MATLAB is retained for the PhD waveform research track where it is the appropriate tool; Python is the appropriate
tool for the operations platform.

---

## Rationale

Python is the only language that spans all four technical domains ILMOP
requires without requiring polyglot architecture:

- **Simulation:** `sgp4`, `numpy`, `scipy` provide production-quality numerical computing
- **Data engineering:** `confluent-kafka`, `psycopg2`/`asyncpg`, `pydantic` provide the full Kafka and database stack
- **Machine learning:** `scikit-learn`, `mlflow`, `pandas` cover the planned Sprint 5 anomaly detection and experiment tracking requirements
- **Web services:** `fastapi`, `streamlit`, `uvicorn` cover the Sprint 4 API and dashboard requirements
- **Cloud deployment:** Azure SDK for Python, Docker, and Kubernetes Python tooling are all first-class

A single-language architecture means one dependency management system (`pip`/`requirements.txt`), one testing framework (`pytest`), one container base image, and one knowledge domain for the developer. For a
platform that spans simulation through AI through cloud deployment, the unified ecosystem is a decisive advantage. 
The Azure deployment target (Sprint 6) is also relevant: Azure Functions, Azure ML, and Azure Kubernetes Service all have first-class Python support.
---

## Consequences

### Positive
- Unified ecosystem across all technical domains
- Fastest iteration speed for a single developer
- Direct integration with the scientific Python stack for PhD research overlap (orbit propagation, signal analysis) All planned libraries (sgp4, confluent-kafka, fastapi, mlflow, streamlit, scikit-learn) are Python-native and actively maintained  Azure, Docker, and Kubernetes tooling all support Python as a first-class deployment target

### Negative / trade-offs
- Python's Global Interpreter Lock (GIL) limits true CPU parallelism;
  multi-satellite simulation at high tick rates may require
  `multiprocessing` rather than `threading`
- Interpreted language: a performance bottleneck discovered late in development may require rewriting hot paths in Cython or delegating to compiled extensions
- Not suitable for any on-board or real-time embedded component —  Python's role is strictly ground software

### Implications for future sprints
- Sprint 5 (MLflow, scikit-learn): fully native, no bridging required - Sprint 6 (Azure/AKS): Python Docker images are well-supported on AKS;
  Azure ML natively tracks MLflow experiments
- PhD integration: `sgp4` orbit truth, `numpy` signal processing, and `scipy` geometric calculations can all interoperate within the same codebase.
