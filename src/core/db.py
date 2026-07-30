"""Database connection pool and shared helpers.

Call ``configure_logging()`` before creating a pool in entry points.
Call ``create_pool`` / ``close_pool`` in the FastAPI lifespan; route handlers
acquire connections via ``deps.get_db`` (which calls ``get_pool`` internally)
or directly via the ``acquire`` async context manager.
"""

import os
import pathlib
import re
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
    min_size = int(os.environ.get("DB_POOL_MIN_SIZE", "2"))
    max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "5"))
    _pool = await asyncpg.create_pool(resolved_dsn, min_size=min_size, max_size=max_size)
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


READY_ACQUIRE_TIMEOUT_S = 2.0


async def check_ready(timeout: float = READY_ACQUIRE_TIMEOUT_S) -> None:
    """Probe pool readiness: bounded acquire + ``SELECT 1``.

    The readiness endpoint's sanctioned direct-pool access (#343) — a probe
    must touch the real pool, and a failing ``Depends(get_db)`` surfaces as a
    500, not a catchable 503. The bounded acquire is load-bearing: a bare
    acquire on an exhausted pool hangs forever, making pool exhaustion
    indistinguishable at the client from process death.

    The query is bounded too: an idle pooled connection acquires instantly,
    then an unbounded ``SELECT`` would hang on a wedged DB (network partition,
    stuck backend) — the same failure mode one layer down.

    Raises:
        RuntimeError: pool not initialised (``DATABASE_URL`` unset).
        TimeoutError: pool exhausted or probe query timed out.
        Exception: any driver/DB failure from the probe query.
    """
    pool = get_pool()
    conn = await pool.acquire(timeout=timeout)
    try:
        await conn.fetchval("SELECT 1", timeout=timeout)
    finally:
        await pool.release(conn)


# ---------------------------------------------------------------------------
# Schema application
# ---------------------------------------------------------------------------

_NON_UNIQUE_CREATE_INDEX_RE = re.compile(r"^\s*CREATE\s+INDEX\b", re.IGNORECASE)


def _parse_schema_statements(sql: str) -> list[str]:
    """Split *sql* into individual statements on top-level semicolons.

    Handles:
    - Dollar-quoted bodies (``$$ ... $$``, ``$tag$ ... $tag$``) — semicolons
      inside DO blocks and function definitions are ignored.
    - Single-quoted string literals — semicolons inside ``'...'`` are ignored.
    - Line comments (``--``) — semicolons inside comments are ignored.
    """
    stmts: list[str] = []
    current: list[str] = []
    dollar_tag: str | None = None
    in_single_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]

        # Inside a dollar-quoted block: look for closing tag, pass through everything.
        if dollar_tag is not None:
            if sql[i:].startswith(dollar_tag):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(ch)
                i += 1
            continue

        # Inside a single-quoted string: look for closing quote (doubled '' is escape).
        if in_single_quote:
            current.append(ch)
            if ch == "'" and sql[i + 1 : i + 2] == "'":
                # Escaped single quote — consume both and stay in string.
                current.append("'")
                i += 2
            elif ch == "'":
                in_single_quote = False
                i += 1
            else:
                i += 1
            continue

        # Line comment: skip to end of line (including the newline).
        if ch == "-" and sql[i + 1 : i + 2] == "-":
            end = sql.find("\n", i)
            if end == -1:
                end = len(sql)
            current.append(sql[i : end + 1])
            i = end + 1
            continue

        # Dollar-quote open detection.
        if ch == "$":
            end = sql.find("$", i + 1)
            if end != -1:
                tag = sql[i : end + 1]
                dollar_tag = tag
                current.append(tag)
                i = end + 1
                continue

        # Single-quote open.
        if ch == "'":
            in_single_quote = True
            current.append(ch)
            i += 1
            continue

        # Top-level semicolon — end of statement.
        if ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                stmts.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Trailing content without a semicolon (comments, whitespace) is discarded.
    return stmts


def _is_non_unique_create_index(stmt: str) -> bool:
    """Return True when *stmt* is a non-unique ``CREATE INDEX`` statement."""
    return bool(_NON_UNIQUE_CREATE_INDEX_RE.match(stmt))


async def apply_schema(conn: asyncpg.Connection) -> None:
    """Apply schema.sql in two phases to minimise write-lock duration.

    **Phase 1 — transactional:** tables, views, triggers, constraints, unique
    indexes, and seed rows are applied atomically.  A failure here rolls the
    entire structural DDL back, leaving the database in its prior state.

    **Phase 2 — concurrent indexes:** non-unique ``CREATE INDEX`` statements
    are executed *outside* any transaction using ``CREATE INDEX CONCURRENTLY``,
    which acquires only a ``ShareUpdateExclusiveLock`` and does not block DML.
    ``IF NOT EXISTS`` semantics are preserved — already-present indexes are
    skipped without error.  If a concurrent build fails the database is still
    structurally sound; re-running ``apply_schema`` retries only the missing
    indexes.  When called on a connection that is already inside an active
    transaction (e.g., test fixtures), Phase 2 falls back to plain
    ``CREATE INDEX`` — ``IF NOT EXISTS`` makes it a no-op for any index that
    already exists.

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
    statements = _parse_schema_statements(sql)

    transactional = [s for s in statements if not _is_non_unique_create_index(s)]
    concurrent = [s for s in statements if _is_non_unique_create_index(s)]

    # Phase 1: structural DDL in one transaction (batch, same as original).
    async with conn.transaction():
        await conn.execute(";\n".join(transactional))

    # Phase 2: non-unique indexes.
    # CONCURRENTLY requires the connection to be outside any transaction block.
    # When called inside an existing transaction (e.g., test fixtures that wrap
    # the connection in a rollback), fall back to plain CREATE INDEX — indexes
    # are already present in that case so IF NOT EXISTS makes it a no-op.
    use_concurrently = not conn.is_in_transaction()
    for stmt in concurrent:
        if use_concurrently:
            # Negative lookahead guards against doubling if the source statement
            # already carries CONCURRENTLY (e.g. a future schema.sql edit).
            stmt = re.sub(
                r"\bCREATE\s+INDEX(?!\s+CONCURRENTLY)\b",
                "CREATE INDEX CONCURRENTLY",
                stmt,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            # Strip CONCURRENTLY when falling back inside a transaction.
            stmt = re.sub(
                r"\bCREATE\s+INDEX\s+CONCURRENTLY\b",
                "CREATE INDEX",
                stmt,
                count=1,
                flags=re.IGNORECASE,
            )
        await conn.execute(stmt)

    await _warn_if_lookup_tables_unseeded(conn)


async def _warn_if_lookup_tables_unseeded(conn: asyncpg.Connection) -> None:
    """Log a WARNING for each BCP 47 / ISO 15924 lookup table that's empty.

    Empty lookup tables block any non-NULL write to ``person_names.locale``
    or ``.script`` via the FK. Seed with::

        uv run --group seed scripts/seed_locales_scripts.py
    """
    for table in ("bcp47_locales", "iso15924_scripts"):
        empty = await conn.fetchval(f"SELECT NOT EXISTS (SELECT 1 FROM {table})")
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
