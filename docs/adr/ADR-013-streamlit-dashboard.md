# ADR-013 — Streamlit for the Operator Dashboard

**Date:** 2024-01-01  
**Status:** Accepted  
**Deciders:** Stephen Ogodo  

---

## Context

ILMOP requires a real-time operator dashboard displaying live satellite telemetry, orbital position maps, trend charts, and anomaly alarm panels. The dashboard must poll the REST API at a configurable interval (2–30 seconds), allow satellite selection, and display alarm severity visually. Development speed is important — the dashboard is a demonstration tool, not a production UI.

---

## Decision

Streamlit is the operator dashboard framework. It polls the FastAPI REST API (Option A — API-first architecture) rather than accessing TimescaleDB directly.

---

## Rationale

Streamlit provides a Python-native reactive UI framework where each widget interaction triggers a full Python re-run (Streamlit Inc., 2024). This reactive model is well-suited to the polling dashboard pattern — `time.sleep(refresh_s)` followed by `st.rerun()` creates a clean auto-refresh loop without JavaScript or WebSocket infrastructure.

The "Option A — API-first" architecture (the dashboard queries the REST API rather than the database directly) was chosen over direct database access for two reasons. First, the REST API provides a stable, versioned interface that decouples the dashboard from the database schema — a schema change in TimescaleDB requires only an API update, not a dashboard update. Second, the REST API will be consumed by other clients in Sprint 6 (navigation validation pipeline) and Sprint 7 (cloud deployment), so building the API correctly is more valuable than a short-term efficiency gain from direct database access.

`st.empty()` containers are used for the alarm panel to prevent stale expander widgets from persisting across satellite dropdown changes — a known Streamlit widget lifecycle issue resolved by ensuring the alarm slot is completely replaced on each rerun.

---

## Consequences

**Positive:**
- Python-native — no JavaScript, HTML, or CSS required for basic charts and panels
- Built-in chart types (line charts, metric cards) cover ILMOP's telemetry trend requirements
- Auto-refresh pattern with `st.rerun()` is simple and reliable
- API-first architecture future-proofs against schema and infrastructure changes

**Negative:**
- Streamlit's full-page rerun on every interaction limits fine-grained UI control
- The alarm panel stale widget issue requires `st.empty()` wrapper to prevent cross-satellite contamination
- Not appropriate for production operator interfaces requiring custom styling or complex interactivity

---

## Alternatives Considered

- **Grafana:** Production-grade monitoring dashboard but requires a separate service, data source configuration, and dashboard JSON maintenance
- **Dash (Plotly):** More flexible but requires more code for equivalent functionality
- **Direct database access:** Simpler short-term but violates the API-first architecture principle

---

## References

- Streamlit Inc. (2024). *Streamlit Documentation — Build data apps in minutes*. https://docs.streamlit.io/
