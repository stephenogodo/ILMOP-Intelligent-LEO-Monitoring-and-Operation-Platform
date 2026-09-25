# ── ILMOP — Alarm Sink ───────────────────────────────────────────────────────
# Consumes alarm records from Kafka (alarms.{satellite_id} topics) and
# persists them to the TimescaleDB alarms hypertable.
#
# Mirrors the telemetry_sink pattern exactly:
#   - Wildcard subscription to all alarms.* topics
#   - Manual Kafka offset commit after successful DB write
#   - Batched writes with idempotent INSERT ON CONFLICT DO NOTHING
#
# Run alongside the anomaly detection service:
#   anomaly_detection publishes to alarms.*
#   alarm_sink consumes from alarms.* and writes to TimescaleDB
#
# Build:
#   docker build -f services/alarm_sink/Dockerfile -t ilmop-alarm-sink .
#
# Run:
#   docker run --rm --env-file .env ilmop-alarm-sink

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP Alarm Sink — Kafka consumer → TimescaleDB alarms table"
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
COPY services/alarm_sink/ ./services/alarm_sink/

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop
USER ilmop

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "from services.alarm_sink.sink import AlarmSink" || exit 1

CMD ["python", "-m", "services.alarm_sink.sink"]