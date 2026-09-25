# ── ILMOP — Mission Control Dashboard ───────────────────────────────────────
# Streamlit operator dashboard. Connects to the ILMOP FastAPI exclusively
# (never directly to TimescaleDB). Also runs the fleet dashboard and
# coverage statistics pages which use services/otfs/ and services/navigation/.
#
# Build:
#   docker build -f services/dashboard/Dockerfile -t ilmop-dashboard .
#
# Run (operator dashboard):
#   docker run --rm --env-file .env -p 8501:8501 ilmop-dashboard
#
# Run (fleet dashboard):
#   docker run --rm --env-file .env -p 8501:8501 ilmop-dashboard \
#       streamlit run services/dashboard/pages/fleet_dashboard.py \
#       --server.port 8501 --server.address 0.0.0.0
#
# Run (coverage statistics):
#   docker run --rm --env-file .env -p 8501:8501 ilmop-dashboard \
#       streamlit run services/dashboard/pages/coverage_statistics.py \
#       --server.port 8501 --server.address 0.0.0.0

FROM python:3.12-slim

LABEL maintainer="Stephen O. Ogodo <stephen.ojo.ogodo@gmail.com>"
LABEL description="ILMOP Dashboard — Streamlit operator interface + fleet + coverage"
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

# Dashboard and its pages
COPY services/dashboard/ ./services/dashboard/

# Fleet dashboard and coverage engine import from otfs/ and navigation/
COPY services/otfs/ ./services/otfs/
COPY services/navigation/ ./services/navigation/

# Constellation configs used by fleet dashboard and coverage engine
COPY config/ ./config/

# ── Streamlit configuration ──────────────────────────────────────────────────
# Disable the "Deploy" button and browser auto-open in a container context
RUN mkdir -p /home/root/.streamlit
RUN echo '[server]\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\n' \
    > /home/root/.streamlit/config.toml

# ── Non-root user ────────────────────────────────────────────────────────────
RUN adduser --disabled-password --gecos "" ilmop && \
    mkdir -p /home/ilmop/.streamlit && \
    echo '[server]\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\n' \
    > /home/ilmop/.streamlit/config.toml && \
    chown -R ilmop:ilmop /home/ilmop
USER ilmop

# ── Expose dashboard port ────────────────────────────────────────────────────
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c \
        "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" \
        || exit 1

# ── Default: operator dashboard ─────────────────────────────────────────────
CMD ["streamlit", "run", "services/dashboard/app.py", \
     "--server.port", "8501", \
     "--server.address", "0.0.0.0"]
