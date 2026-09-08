"""GET /health — liveness check."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from asyncpg import Pool
from services.api.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health", summary="API liveness check")
async def health(pool: Pool = Depends(get_pool)):
    """
    Returns the API status and a UTC timestamp.
    Also verifies the database connection is alive.
    """
    db_ok = False
    try:
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass

    return {
        "status":    "ok" if db_ok else "degraded",
        "database":  "connected" if db_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version":   "0.4.0",
    }
