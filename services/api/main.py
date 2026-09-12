"""
services/api/main.py

ILMOP Telemetry API — FastAPI application entry point.

Start with:
    uvicorn services.api.main:app --host 0.0.0.0 --port 8000 --reload

Interactive docs available at:
    http://localhost:8000/docs    (Swagger UI)
    http://localhost:8000/redoc   (ReDoc)
"""

import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import settings
from services.api.cache import init_cache, close_cache
from services.api.routers import health, satellites, telemetry, alarms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


# ── Application lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage shared resources across the application lifetime.
    Startup:  create asyncpg pool, connect Redis cache.
    Shutdown: close pool, close Redis connection.
    """
    log.info("ILMOP API starting up …")

    app.state.pool = await asyncpg.create_pool(
        settings.timescaledb_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    log.info("asyncpg pool created | db=%s", settings.timescaledb_name)

    await init_cache(app)

    yield   # ← application runs here

    log.info("ILMOP API shutting down …")
    await app.state.pool.close()
    await close_cache(app)
    log.info("Shutdown complete.")


# ── FastAPI application ────────────────────────────────────────────────────────

app = FastAPI(
    title       = "ILMOP Telemetry API",
    description = (
        "REST interface for the Intelligent LEO Monitoring and Operation Platform. "
        "Provides access to real-time and historical satellite telemetry stored in "
        "TimescaleDB.  Consumed by the Streamlit dashboard and any external client."
    ),
    version     = "0.4.0",
    lifespan    = lifespan,
)

# CORS — allow all origins for development; tighten for Sprint 6 production
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = False,
    allow_methods     = ["GET"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(satellites.router)
app.include_router(telemetry.router)
app.include_router(alarms.router)


# ── Dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.api.main:app",
        host    = settings.api_host,
        port    = settings.api_port,
        reload  = True,
        log_level = "info",
    )
