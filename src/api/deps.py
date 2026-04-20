"""Shared FastAPI dependencies (available to all API sub-packages)."""

import src.core.db as db


async def get_db():
    """Yield a connection from the global asyncpg pool."""
    pool = db.get_pool()
    async with pool.acquire() as conn:
        yield conn
