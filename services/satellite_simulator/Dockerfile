# ── ILMOP — Satellite Simulator ─────────────────────────────────────────────
# Runs run_demo.py: SGP4 orbit propagation, eclipse-aware physics models,
# Kafka telemetry producer for all four constellation scenarios.
#
# Build:
#   docker build -f services/satellite_simulator/Dockerfile -t ilmop-simulator .
#
# Run (scenario 1, real-time):
#   docker run --rm --env-file .env ilmop-simulator python run_demo.py --scenario 1
#
# Run (scenario 3, 60× speed):
#   docker run --rm --env-file .env ilmop-simulator \
#       python run_demo.py --scenario 3 --speed 60

FROM python:3.12-slim

# Metadata
LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP Satellite Simulator — SGP4 + Kafka telemetry producer"
LABEL version="1.0.0"

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# ── Layer 1: Install dependencies (cached unless requirements.txt changes) ───
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Layer 2: Copy source ─────────────────────────────────────────────────────
# shared/ — config and schemas used by every service
COPY shared/ ./shared/

# The simulator service itself
COPY services/satellite_simulator/ ./services/satellite_simulator/

# Constellation scenario YAML files (scenario_1 through scenario_4)
COPY config/ ./config/

# Main entry point
COPY run_demo.py .

# ── Non-root user for security ───────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop
USER ilmop

# ── Default command: Scenario 1, real-time ──────────────────────────────────
# Override at runtime with --scenario N --speed M
CMD ["python", "run_demo.py", "--scenario", "1"]
