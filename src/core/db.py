"""Database connection pool and shared helpers.

Call ``configure_logging()`` before creating a pool in entry points.
Call ``create_pool`` / ``close_pool`` in the FastAPI lifespan; route handlers
acquire connections via ``deps.get_db`` (which calls ``get_pool`` internally)
or directly via the ``acquire`` async context manager.
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
        dsn: PostgreSQL DSN. Falls back to ``DATABASE_URL`` env var
            (set in ``/etc/power-map/.env``).

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

    The BCP 47 / ISO 15924 lookup tables (``bcp47_locales``,
    ``iso15924_scripts``) are created but left empty here — seeding them
    requires the optional ``seed`` dep group (langcodes + pycountry) and
    runs once per environment via:

        uv run --group seed scripts/seed_locales_scripts.py

    A WARNING is logged when either lookup table is empty after apply,
    since the live FK on ``person_names.locale`` / ``.script`` will
    reject any non-NULL write until the tables are populated.
    """
    sql = SCHEMA_PATH.read_text()
    async with conn.transaction():
        await conn.execute(sql)
    await _warn_if_lookup_tables_unseeded(conn)


async def _warn_if_lookup_tables_unseeded(conn: asyncpg.Connection) -> None:
    """Log a WARNING for each BCP 47 / ISO 15924 lookup table that's empty.

    Empty lookup tables block any non-NULL write to ``person_names.locale``
    or ``.script`` via the FK. Seed with::

        uv run --group seed scripts/seed_locales_scripts.py
    """
    for table in ("bcp47_locales", "iso15924_scripts"):
        empty = await conn.fetchval(
            f"SELECT NOT EXISTS (SELECT 1 FROM {table} LIMIT 1)"
        )
        if empty:
            logger.warning(
                "%s is empty — run `uv run --group seed scripts/seed_locales_scripts.py` "
                "before any non-NULL person_names.locale/.script writes",
                table,
            )


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_id() -> str:
    """Return a new ULID as a 26-character Crockford base32 string."""
    return str(ULID())


def visible_names_filter(alias: str | None = None) -> str:
    """SQL fragment to AND-append when querying person_names directly.

    Excludes deadnames and any row marked legal_only / hidden / internal,
    matching the visibility rule documented in docs/CONVENTIONS.md.

    Args:
        alias: Optional table alias used in the query (e.g. 'pn' for
            ``FROM person_names pn``). When ``None``, the column is
            unqualified.
    """
    col = f"{alias}.visibility" if alias else "visibility"
    return f"{col} = 'public'"
