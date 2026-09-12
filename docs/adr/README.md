# Architecture Decision Records — ILMOP

This directory contains the Architecture Decision Records (ADRs) for the
Intelligent LEO Monitoring and Operation Platform. Each ADR documents a
significant technical decision: what was decided, what alternatives were
seriously considered, why this option was chosen, and what the consequences
and trade-offs are.

ADRs are written at the time of implementation and serve three purposes:

1. **Technical memory** — so future contributors (or a future version of the
   lead developer) understand why the system is built the way it is, not just
   how it works.

2. **Accountability** — so every architectural choice can be defended on its
   merits, not just its familiarity.

3. **Research record** — for the PhD research track, ADRs provide the
   documented rationale that examiners and reviewers expect.

---

## Decision log

| ADR | Title | Sprint | Status |
|-----|-------|--------|--------|
| [ADR-001](ADR-001-python-primary-language.md) | Python as primary implementation language | 1 | Accepted |
| [ADR-002](ADR-002-pydantic-v2-schema-validation.md) | Pydantic v2 for telemetry schema validation | 1 | Accepted |
| [ADR-003](ADR-003-kafka-event-streaming-backbone.md) | Apache Kafka as the event streaming backbone | 1 | Accepted |
| [ADR-004](ADR-004-timescaledb-telemetry-storage.md) | TimescaleDB for time-series telemetry storage | 1 | Accepted |
| [ADR-005](ADR-005-confluent-kafka-python-client.md) | confluent-kafka as the Python Kafka client | 1 | Accepted |
| [ADR-006](ADR-006-sgp4-orbit-propagation.md) | SGP4 for satellite orbit propagation | 2 | Accepted |
| [ADR-007](ADR-007-eclipse-aware-physics-models.md) | Eclipse-aware battery and thermal physics models | 2 | Accepted |
| [ADR-008](ADR-008-cylindrical-shadow-eclipse-detection.md) | Cylindrical shadow model for eclipse detection | 2 | Accepted |
| [ADR-009](ADR-009-satellite-domain-object-pattern.md) | Satellite dataclass as stateful domain object | 2 | Accepted |
| [ADR-010](ADR-010-pydantic-settings-configuration.md) | pydantic-settings for centralised configuration management | 3 | Accepted |
| [ADR-011](ADR-011-psycopg2-timescaledb-driver.md) | psycopg2 (synchronous) as TimescaleDB driver for the sink | 3 | Accepted |
| [ADR-012](ADR-012-fastapi-rest-layer.md) | FastAPI as the REST API framework | 4 | Accepted |
| [ADR-013](ADR-013-streamlit-dashboard.md) | Streamlit as the operator dashboard framework | 4 | Accepted |

---

## ADR format

Each ADR follows this structure:

- **Status** — Accepted / Superseded / Deprecated
- **Sprint** — which sprint introduced this decision
- **Context** — the situation that made this decision necessary
- **Decision** — what was decided, stated plainly
- **Alternatives considered** — other options that were evaluated and why they were rejected
- **Rationale** — the reasoning that led to this choice
- **Consequences** — benefits, trade-offs, and implications for future sprints
