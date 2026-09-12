"""GET /alarms/{satellite_id} — recent alarms from the anomaly detection service."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from asyncpg import Pool

from services.api.db import get_pool

router = APIRouter(prefix="/alarms", tags=["alarms"])


@router.get(
    "/{satellite_id}",
    summary="Recent anomaly alarms for a satellite",
)
async def get_alarms(
    satellite_id: str,
    hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Look-back window in hours (default 24, max 168 = 7 days)",
    ),
    severity: Optional[str] = Query(
        default=None,
        description="Filter by severity: INFO, WARNING, or CRITICAL",
    ),
    pool: Pool = Depends(get_pool),
):
    """
    Returns anomaly alarms for a satellite within the specified time window.

    Alarms are produced by the Sprint 5 anomaly detection service and stored
    in the alarms table.  If no alarms table exists yet (pre-Sprint 5
    deployment), returns an empty list rather than an error.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        if severity:
            rows = await pool.fetch("""
                SELECT
                    satellite_id, timestamp, orbit_type,
                    severity, alarm_type, parameter,
                    observed_value, expected_min, expected_max,
                    anomaly_score, model_version, message,
                    from_fault_injection
                FROM alarms
                WHERE satellite_id = $1
                  AND timestamp > $2
                  AND severity = $3
                ORDER BY timestamp DESC
                LIMIT 200
            """, satellite_id, since, severity.upper())
        else:
            rows = await pool.fetch("""
                SELECT
                    satellite_id, timestamp, orbit_type,
                    severity, alarm_type, parameter,
                    observed_value, expected_min, expected_max,
                    anomaly_score, model_version, message,
                    from_fault_injection
                FROM alarms
                WHERE satellite_id = $1
                  AND timestamp > $2
                ORDER BY timestamp DESC
                LIMIT 200
            """, satellite_id, since)

        result = []
        for row in rows:
            d = dict(row)
            if hasattr(d.get("timestamp"), "isoformat"):
                d["timestamp"] = d["timestamp"].isoformat()
            result.append(d)
        return result

    except Exception as e:
        # Alarms table may not exist yet (pre-Sprint 5 deployment)
        if "alarms" in str(e).lower() and "exist" in str(e).lower():
            return []
        raise HTTPException(status_code=500, detail=str(e))
