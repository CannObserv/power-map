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
import phonenumbers
from email_validator import EmailNotValidError
from email_validator import validate_email as _ev_validate
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


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------


def normalize_phone(raw: str, default_region: str = "US") -> str:
    """Parse *raw* and return an E.164-formatted phone number.

    Args:
        raw: Raw phone string (e.g. "(206) 555-1234").
        default_region: ISO 3166-1 alpha-2 region hint for numbers without
            a country code prefix. Defaults to ``"US"``.

    Returns:
        E.164 string, e.g. ``"+12065551234"``.

    Raises:
        ValueError: If *raw* cannot be parsed as a valid phone number.
    """
    if not raw or not raw.strip():
        raise ValueError("invalid phone number: empty input")
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"invalid phone number: {raw!r}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(f"invalid phone number: {raw!r}")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------


def validate_email(raw: str) -> str:
    """Validate *raw* as an email address and return a normalised form.

    The domain part is lowercased; the local part is preserved as-is per
    RFC 5321.

    Args:
        raw: Raw email string.

    Returns:
        Normalised email address.

    Raises:
        ValueError: If *raw* is not a valid email address.
    """
    if not raw or not raw.strip():
        raise ValueError("invalid email address: empty input")
    try:
        info = _ev_validate(raw, check_deliverability=False)
        return info.normalized
    except EmailNotValidError as exc:
        raise ValueError(f"invalid email address: {raw!r}") from exc
