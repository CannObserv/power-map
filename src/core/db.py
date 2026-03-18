"""Database connection pool and shared helpers.

Call ``configure_logging()`` before creating a pool in entry points.
Import ``get_pool`` in route handlers; call ``create_pool`` / ``close_pool``
in the FastAPI lifespan.
"""

import os
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from ulid import ULID

from src.core.logging import get_logger

logger = get_logger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create and cache the global connection pool.

    Args:
        dsn: PostgreSQL DSN. Falls back to ``DATABASE_URL`` env var.

    Returns:
        The asyncpg connection pool.
    """
    global _pool
    resolved_dsn = dsn or os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(resolved_dsn)
    logger.info("database pool created")
    return _pool


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("database pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the active pool; raises ``RuntimeError`` if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call create_pool() first.")
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """Async context manager that acquires a connection from the pool."""
    async with get_pool().acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Schema application
# ---------------------------------------------------------------------------


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Execute schema.sql against *conn* inside a transaction.

    Wrapping in a transaction ensures the schema is applied atomically:
    either all tables, indexes, triggers, and seed rows are created, or
    none are (on error the whole transaction rolls back).
    """
    sql = SCHEMA_PATH.read_text()
    async with conn.transaction():
        await conn.execute(sql)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_id() -> str:
    """Return a new ULID as a 26-character Crockford base32 string."""
    return str(ULID())

