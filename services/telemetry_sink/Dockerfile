# ── ILMOP — Telemetry Sink ───────────────────────────────────────────────────
# Consumes telemetry records from Kafka and persists them to the
# TimescaleDB hypertable. Runs as a long-lived consumer loop.
#
# Build:
#   docker build -f services/telemetry_sink/Dockerfile -t ilmop-sink .
#
# Run:
#   docker run --rm --env-file .env ilmop-sink

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP Telemetry Sink — Kafka consumer → TimescaleDB writer"
LABEL version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# ── Layer 1: Dependencies ────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Layer 2: Source ──────────────────────────────────────────────────────────
COPY shared/ ./shared/
COPY services/telemetry_sink/ ./services/telemetry_sink/

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop
USER ilmop

# ── Health check: verify Python can import the sink module ───────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "from services.telemetry_sink.sink import TelemetrySink" || exit 1

CMD ["python", "-m", "services.telemetry_sink.sink"]
