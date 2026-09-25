# ── ILMOP — Anomaly Detection ────────────────────────────────────────────────
# Real-time anomaly detection service. Consumes telemetry from Kafka,
# routes records to the appropriate Isolation Forest model (LEO or HEO),
# and publishes Alarm records when anomalies are detected.
#
# MLflow model artefacts are NOT baked into the image — they are mounted
# as a volume at runtime (local development) or read from Azure Blob
# Storage via MLFLOW_TRACKING_URI (production).
#
# Build:
#   docker build -f services/anomaly_detection/Dockerfile -t ilmop-anomaly .
#
# Run (with local mlruns/ volume):
#   docker run --rm --env-file .env \
#       -v $(pwd)/mlruns:/app/mlruns \
#       ilmop-anomaly
#
# Train a model first (outside the container or add to CMD):
#   docker run --rm --env-file .env \
#       -v $(pwd)/mlruns:/app/mlruns \
#       ilmop-anomaly \
#       python -m services.anomaly_detection.train --orbit-type LEO_CIRCULAR

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP Anomaly Detection — Isolation Forest real-time scorer"
LABEL version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    MLFLOW_TRACKING_URI=mlruns

WORKDIR /app

# ── Layer 1: Dependencies ────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Layer 2: Source ──────────────────────────────────────────────────────────
COPY shared/ ./shared/
COPY services/anomaly_detection/ ./services/anomaly_detection/

# ── MLflow artefact volume mount point ──────────────────────────────────────
# mlruns/ is intentionally excluded from the image (see .dockerignore).
# Mount at runtime: -v $(pwd)/mlruns:/app/mlruns
# Or override MLFLOW_TRACKING_URI for Azure Blob Storage.
RUN mkdir -p mlruns

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop && \
    chown ilmop:ilmop mlruns
USER ilmop

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "from services.anomaly_detection.detector import AnomalyDetector" || exit 1

CMD ["python", "-m", "services.anomaly_detection.detector"]
