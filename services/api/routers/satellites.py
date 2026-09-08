"""GET /satellites — list satellites with recent telemetry."""

from fastapi import APIRouter, Depends
from asyncpg import Pool
from services.api.db import get_pool

router = APIRouter(prefix="/satellites", tags=["satellites"])


@router.get("", summary="List all satellites with recent telemetry")
async def list_satellites(pool: Pool = Depends(get_pool)):
    """
    Returns a list of satellites that have submitted telemetry in the
    last 24 hours, with their latest known status.
    """
    rows = await pool.fetch("""
        SELECT DISTINCT ON (satellite_id)
            satellite_id,
            time        AS last_seen,
            battery_pct,
            temperature_c,
            altitude_km,
            in_eclipse,
            in_contact,
            safe_mode,
            anomaly_flag
        FROM telemetry
        WHERE time > NOW() - INTERVAL '24 hours'
        ORDER BY satellite_id, time DESC
    """)

    return [dict(row) for row in rows]
