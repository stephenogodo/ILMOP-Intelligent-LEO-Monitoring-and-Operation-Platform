# ── ILMOP — REST API ─────────────────────────────────────────────────────────
# FastAPI application serving telemetry, alarms, satellite, and health
# endpoints. Connects to TimescaleDB via asyncpg and caches latest
# records in Redis.
#
# Build:
#   docker build -f services/api/Dockerfile -t ilmop-api .
#
# Run:
#   docker run --rm --env-file .env -p 8000:8000 ilmop-api
#
# API docs (once running):
#   http://localhost:8000/docs   (Swagger UI)
#   http://localhost:8000/redoc  (ReDoc)

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP REST API — FastAPI + asyncpg + Redis cache"
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
COPY services/api/ ./services/api/

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop
USER ilmop

# ── Expose API port ──────────────────────────────────────────────────────────
EXPOSE 8000

# ── Health check: call the /health endpoint ──────────────────────────────────
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
        || exit 1

# ── Production server: Uvicorn, 2 workers, no reload ────────────────────────
# Use --host 0.0.0.0 so the container is reachable from outside.
# Override workers via environment: UVICORN_WORKERS=4
CMD ["uvicorn", "services.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "2"]
