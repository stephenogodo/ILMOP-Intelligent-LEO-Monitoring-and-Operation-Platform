"""
services/api/routers/telemetry.py

Telemetry query endpoints.

GET /telemetry/{satellite_id}/latest     — most recent record (Redis-cached)
GET /telemetry/{satellite_id}            — time-range query
GET /telemetry/{satellite_id}/summary    — 5-minute aggregated buckets
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from services.api.db import get_pool
from services.api.cache import get_cache, cache_get, cache_set

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# ── Helpers ───────────────────────────────────────────────────────────────────

def _record_to_dict(row: asyncpg.Record) -> dict:
    """
    Convert an asyncpg Record to a JSON-serialisable dict.
    Renames the DB column 'time' to 'timestamp' to match the Telemetry schema.
    """
    d = dict(row)
    if "time" in d:
        d["timestamp"] = d.pop("time")
    # asyncpg returns datetime objects; convert to ISO strings for JSON
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/{satellite_id}/latest",
    summary="Latest telemetry record for a satellite",
)
async def get_latest(
    satellite_id: str,
    pool  = Depends(get_pool),
    cache = Depends(get_cache),
):
    """
    Returns the most recent telemetry record for the given satellite.
    Result is cached in Redis for cache_ttl_s seconds (default 5 s).
    """
    cache_key = f"latest:{satellite_id}"

    # 1 — Cache hit
    cached = await cache_get(cache, cache_key)
    if cached:
        return cached

    # 2 — Database query
    row = await pool.fetchrow("""
        SELECT
            time, satellite_id,
            latitude_deg, longitude_deg, altitude_km,
            in_eclipse, in_contact,
            battery_pct, battery_voltage_v, solar_panel_power_w,
            temperature_c,
            cpu_utilization_pct, memory_utilization_pct,
            downlink_rate_mbps, uplink_rate_mbps,
            safe_mode, anomaly_flag
        FROM telemetry
        WHERE satellite_id = $1
        ORDER BY time DESC
        LIMIT 1
    """, satellite_id)

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No telemetry found for satellite '{satellite_id}'",
        )

    result = _record_to_dict(row)

    # 3 — Populate cache for next request
    await cache_set(cache, cache_key, result)

    return result


@router.get(
    "/{satellite_id}",
    summary="Telemetry history for a satellite",
)
async def get_history(
    satellite_id: str,
    from_time: Optional[datetime] = Query(
        default=None,
        alias="from",
        description="Start of time range (ISO 8601 UTC). Defaults to 1 hour ago.",
    ),
    to_time: Optional[datetime] = Query(
        default=None,
        alias="to",
        description="End of time range (ISO 8601 UTC). Defaults to now.",
    ),
    limit: int = Query(
        default=500,
        ge=1,
        le=5000,
        description="Maximum number of records to return.",
    ),
    pool = Depends(get_pool),
):
    """
    Returns telemetry records for a satellite within a time range,
    ordered oldest-first.  Default window is the last hour.
    """
    now      = datetime.now(timezone.utc)
    t_to     = to_time   or now
    t_from   = from_time or (now - timedelta(hours=1))

    rows = await pool.fetch("""
        SELECT
            time, satellite_id,
            latitude_deg, longitude_deg, altitude_km,
            in_eclipse, in_contact,
            battery_pct, battery_voltage_v, solar_panel_power_w,
            temperature_c,
            cpu_utilization_pct, memory_utilization_pct,
            downlink_rate_mbps, uplink_rate_mbps,
            safe_mode, anomaly_flag
        FROM telemetry
        WHERE satellite_id = $1
          AND time BETWEEN $2 AND $3
        ORDER BY time ASC
        LIMIT $4
    """, satellite_id, t_from, t_to, limit)

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No telemetry found for satellite '{satellite_id}' in the requested range",
        )

    return [_record_to_dict(row) for row in rows]


@router.get(
    "/{satellite_id}/summary",
    summary="5-minute aggregated telemetry summary",
)
async def get_summary(
    satellite_id: str,
    from_time: Optional[datetime] = Query(
        default=None,
        alias="from",
        description="Start of time range (ISO 8601 UTC). Defaults to 6 hours ago.",
    ),
    to_time: Optional[datetime] = Query(
        default=None,
        alias="to",
        description="End of time range. Defaults to now.",
    ),
    pool = Depends(get_pool),
):
    """
    Returns pre-computed 5-minute bucket averages from the TimescaleDB
    continuous aggregate view (telemetry_5min).  Much faster than querying
    raw records for long time windows — used by dashboard trending charts.
    """
    now    = datetime.now(timezone.utc)
    t_to   = to_time   or now
    t_from = from_time or (now - timedelta(hours=6))

    rows = await pool.fetch("""
        SELECT
            bucket AS time,
            satellite_id,
            avg_battery_pct,
            avg_battery_voltage_v,
            avg_solar_power_w,
            avg_temperature_c,
            avg_cpu_pct,
            avg_memory_pct,
            avg_altitude_km,
            any_eclipse,
            any_contact,
            sample_count
        FROM telemetry_5min
        WHERE satellite_id = $1
          AND bucket BETWEEN $2 AND $3
        ORDER BY bucket ASC
    """, satellite_id, t_from, t_to)

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No summary data for satellite '{satellite_id}' in range",
        )

    return [_record_to_dict(row) for row in rows]
