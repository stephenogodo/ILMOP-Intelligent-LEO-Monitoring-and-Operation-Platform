"""
services/api/cache.py

Redis cache helpers for the ILMOP API.

The cache stores the latest telemetry record per satellite as a JSON
string with a short TTL (default 5 seconds).  On a GET /latest request
the API checks the cache first; on a miss it queries TimescaleDB and
populates the cache for the next request.

Redis is optional — if it is unavailable at startup the API continues
without caching (all requests fall through to TimescaleDB).  This makes
the API resilient to Redis being down during development or on first run.
"""

import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Request

from shared.config import settings

log = logging.getLogger(__name__)


async def init_cache(app) -> None:
    """
    Connect to Redis at startup.  Sets app.state.redis to the client
    on success, or None if Redis is unavailable.
    """
    try:
        client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        await client.ping()
        app.state.redis = client
        log.info("Redis cache connected: %s", settings.redis_url)
    except Exception as exc:
        log.warning("Redis unavailable — caching disabled: %s", exc)
        app.state.redis = None


async def close_cache(app) -> None:
    """Close the Redis connection at shutdown."""
    client = getattr(app.state, "redis", None)
    if client:
        await client.aclose()


async def get_cache(request: Request) -> Optional[aioredis.Redis]:
    """
    FastAPI dependency — injects the Redis client, or None if unavailable.

    Usage in a router:
        @router.get("/example")
        async def example(cache = Depends(get_cache)):
            if cache:
                cached = await cache.get("key")
    """
    return getattr(request.app.state, "redis", None)


async def cache_get(cache: Optional[aioredis.Redis], key: str) -> Optional[dict]:
    """Return a cached dict by key, or None on miss or error."""
    if not cache:
        return None
    try:
        value = await cache.get(key)
        return json.loads(value) if value else None
    except Exception as exc:
        log.warning("Cache read failed for key=%s: %s", key, exc)
        return None


async def cache_set(
    cache: Optional[aioredis.Redis],
    key: str,
    data: dict,
) -> None:
    """Store a dict in the cache with the configured TTL."""
    if not cache:
        return
    try:
        await cache.setex(key, settings.cache_ttl_s, json.dumps(data, default=str))
    except Exception as exc:
        log.warning("Cache write failed for key=%s: %s", key, exc)
