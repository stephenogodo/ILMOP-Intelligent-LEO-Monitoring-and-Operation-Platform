"""
services/api/db.py

asyncpg connection pool management.

The pool is created once at application startup (via the FastAPI lifespan
context manager in main.py) and stored in app.state.pool.  Every request
that needs a database connection gets one from the pool via the get_pool()
dependency — FastAPI resolves it automatically for any route that declares
it as a parameter.
"""

import asyncpg
from fastapi import Request


async def get_pool(request: Request) -> asyncpg.Pool:
    """
    FastAPI dependency — injects the shared asyncpg connection pool.

    Usage in a router:
        @router.get("/example")
        async def example(pool: asyncpg.Pool = Depends(get_pool)):
            row = await pool.fetchrow("SELECT ...")
    """
    return request.app.state.pool
