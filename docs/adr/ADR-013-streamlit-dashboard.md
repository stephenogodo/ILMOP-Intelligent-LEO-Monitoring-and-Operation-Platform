# ADR-013: Streamlit as the operator dashboard framework

**Status:** Accepted
**Sprint:** 4
**Date:** 2026-08-25
**Decider:** ILMOP Project

---

## Context

Sprint 4 introduces the first human-facing interface: an operator
dashboard that displays real-time satellite telemetry, historical
trending charts, orbital position, and (in Sprint 5) anomaly alarms.
The dashboard auto-refreshes every few seconds as new telemetry arrives.

The framework must:
1. Produce a usable, informative dashboard with minimal frontend code —
   this is a single-developer research platform, not a product with a
   dedicated frontend team
2. Render real-time line charts that update as new telemetry arrives
3. Display a map with the satellite's current ground track position
4. Consume data from the FastAPI REST layer (Option A architecture —
   the dashboard is a pure API client)
5. Be runnable locally with a single command and deployable to Azure
   as a Docker container in Sprint 6

A key architectural constraint (from ADR-012 and the Option A decision)
is that the dashboard must not connect to TimescaleDB directly. All data
flows through the FastAPI layer.

---

## Decision

Streamlit is the dashboard framework. The dashboard is implemented as
a single Python script (`services/dashboard/app.py`) that calls the
ILMOP FastAPI using the `requests` library and auto-refreshes via
`st.rerun()`.

---

## Alternatives considered

### Grafana
The industry standard for time-series metrics dashboards. Connects
directly to TimescaleDB (or any PostgreSQL-compatible source) via a
data source plugin. Rich built-in visualisations: panels, alerts,
annotations, and a sophisticated query editor.

**Rejected because:** Grafana connects directly to TimescaleDB — it
is an Option B architecture (dashboard bypasses the API). This violates
the single-gateway principle established in ADR-012. Additionally,
Grafana's dashboards are configured through a JSON definition language
or a GUI, not Python code — there is no natural way to add custom
Python business logic (e.g. overlaying anomaly scores from the MLflow
model onto a chart) without building a custom Grafana plugin. For a
research platform where the dashboard will grow to include ML-derived
annotations, Python-native code is the correct foundation.

Grafana is worth reconsidering for a production deployment (Sprint 6+)
where a dedicated ops team manages the dashboards — its multi-user
access control and alerting integration are mature. For Sprint 4
through Sprint 5, Streamlit is faster to develop and more flexible.

### Plotly Dash
A Python-native framework for analytical dashboards. Built on React
and Flask; supports rich interactive visualisations via Plotly. Used
in production at many data science organisations.

**Considered seriously.** Dash produces more polished dashboards than
Streamlit and supports more complex interactivity (callbacks, client-
side JavaScript, real-time WebSocket updates).

**Not chosen because:** Dash's callback model (decorators that define
input/output relationships between components) has a steeper learning
curve than Streamlit's linear script model. For a single-developer
project, Streamlit's "just run a Python script" model produces a
working dashboard faster. Dash's real-time update mechanism (periodic
callbacks via `dcc.Interval`) is more controllable than Streamlit's
`st.rerun()` but also more verbose. Dash is the correct choice if the
dashboard grows to require sub-second update rates or complex
client-side interactivity — at that point, migrating from Streamlit
to Dash is a well-understood task.

### React (custom frontend)
A custom frontend built with React, consuming the FastAPI REST layer
via `fetch()`. This is how production satellite ground systems are
built — a dedicated frontend team builds a custom HMI.

**Rejected because:** React requires a JavaScript build pipeline
(Node.js, webpack/Vite, npm), a separate deployment artifact, and
JavaScript knowledge separate from the Python codebase. For a
single-developer research platform, a custom React frontend is weeks
of work that produces the same functional result as a Streamlit app
written in one day. The Option A architecture (dashboard as API client)
means a future React migration is straightforward — the API contract
is unchanged, only the client changes.

### Panel (HoloViz)
A Python dashboarding library built on Bokeh. Supports reactive
programming, complex layouts, and a wider range of widgets than
Streamlit.

**Rejected because:** Panel's reactive programming model, while
powerful, requires more boilerplate than Streamlit for a simple
auto-refreshing dashboard. Its community and documentation, while good,
are smaller than Streamlit's. Streamlit's native support for
`st.map()`, `st.metric()`, and `st.line_chart()` covers all of
ILMOP's Sprint 4 dashboard requirements with minimal code.

---

## Rationale

Streamlit is chosen for three reasons:

**1. Minimal code for maximum function.** The entire Sprint 4 dashboard
— live metrics row, ground track map, four trending charts, sidebar
satellite selector, auto-refresh — is implemented in approximately
150 lines of Python. A comparable Dash or custom React implementation
would require 3–5× more code. For a research platform, development
velocity matters.

**2. Pure API client.** Streamlit uses the `requests` library to call
the FastAPI — it is the correct Option A architecture. The dashboard
has no database connection, no Kafka consumer, and no knowledge of
the underlying storage layer. When the FastAPI adds caching, rate
limiting, or authentication in Sprint 6, the dashboard inherits all
of it automatically.

**3. Python-native extensibility.** In Sprint 5, the dashboard will
overlay anomaly detection scores on telemetry charts. In the PhD
integration, it will display OFDM navigation residuals alongside
orbital truth. These extensions are trivial in Streamlit — call an
API endpoint, get data as a Python dict, pass to `st.line_chart()`.
In Grafana or a custom React app, the same extensions require building
and deploying new plugins or components.

**Auto-refresh mechanism:** `time.sleep(refresh_s)` followed by
`st.rerun()` at the bottom of the script causes Streamlit to re-execute
the entire script after the sleep interval. Each execution calls
`GET /telemetry/{sat_id}/latest` and `GET /telemetry/{sat_id}/summary`,
which are served from the Redis cache (5-second TTL) — most refreshes
return cached data with sub-millisecond latency.

**Startup command:**
```bash
streamlit run services/dashboard/app.py
```
The dashboard is available at `http://localhost:8501`.

---

## Consequences

### Positive
- A fully functional real-time operator dashboard in ~150 lines of
  Python, runnable with a single command
- Pure API client — no direct database connection; Option A
  architecture is cleanly enforced
- Python-native: Sprint 5 anomaly overlays and PhD payload charts
  are added with a few additional API calls and `st.line_chart()` calls
- `st.map()` renders a satellite position marker on a world map with
  one line of code
- `st.metric()` renders the six live status indicators (battery,
  temperature, solar power, CPU, eclipse, contact) with delta
  comparisons automatically
- Deployable as a Docker container on AKS (Sprint 6) with no build
  pipeline

### Negative / trade-offs
- `st.rerun()` re-executes the entire Python script on each refresh —
  all API calls are repeated, not just the changed widgets. At a 2-second
  refresh interval this is acceptable; at sub-second rates it becomes
  inefficient (Dash's `dcc.Interval` callback is more surgical)
- Streamlit's layout system is less flexible than React or Dash — custom
  CSS is possible but not idiomatic
- Multi-user support requires Streamlit Community Cloud or a reverse
  proxy with authentication — there is no built-in access control
- The `time.sleep()` approach means the Python process is blocked for
  the refresh interval; this is a single-user dashboard, not a
  multi-concurrent-user application

### Implications for future sprints
- Sprint 5 (anomaly detection): add an `alarms` section to the
  dashboard — call `GET /alarms/{sat_id}`, display as a dataframe
  with severity colour-coding; overlay anomaly flags on the battery
  and temperature charts using `st.line_chart()` with a secondary
  series
- Sprint 6 (Azure): the dashboard runs as a Docker container on AKS;
  `settings.api_base_url` points to the internal AKS service DNS name
  (`http://ilmop-api-service:8000`) rather than `localhost:8000`
- PhD integration: a second tab (`st.tabs()`) displays OFDM payload
  telemetry — SNR, Doppler, ranging residuals, waveform mode — served
  from a new `/payload/{sat_id}` FastAPI router, rendered with the
  same Streamlit chart primitives
