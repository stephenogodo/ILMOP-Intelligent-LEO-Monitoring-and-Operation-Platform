# ── ILMOP — ILP Ground Station Scheduler ────────────────────────────────────
# Sprint 7 deliverable. Replaces the random-duration contact timer with a
# geometry-driven ILP scheduler (PuLP + CBC) that:
#   - Computes contact windows from SGP4 orbital mechanics + ground station
#     coordinates (seven stations, elevation >= 5°)
#   - Resolves antenna conflicts when multiple satellites are visible
#   - Publishes the optimised schedule to the passes.schedule Kafka topic
#
# Build:
#   docker build -f services/scheduler/Dockerfile -t ilmop-scheduler .
#
# Run:
#   docker run --rm --env-file .env ilmop-scheduler
#
# Architecture Decision: ADR-020

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP ILP Scheduler — geometry-driven contact window optimiser"
LABEL version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# ── Layer 1: Dependencies ────────────────────────────────────────────────────
# pulp is needed for the ILP model. Add it to requirements.txt before building.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir pulp

# ── Layer 2: Source ──────────────────────────────────────────────────────────
COPY shared/ ./shared/
COPY services/scheduler/ ./services/scheduler/

# Satellite SGP4 models are loaded from constellation YAMLs
COPY services/satellite_simulator/ ./services/satellite_simulator/
COPY config/ ./config/

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop
USER ilmop

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "from services.scheduler.scheduler import main" || exit 1

CMD ["python", "-m", "services.scheduler.scheduler"]
