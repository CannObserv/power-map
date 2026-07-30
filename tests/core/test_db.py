"""Tests for src.core.db — schema helpers and ULID generation."""

import logging
import os
import re
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

import src.core.db as db_module
from src.core.db import (
    _is_non_unique_create_index,
    _parse_schema_statements,
    _warn_if_lookup_tables_unseeded,
    apply_schema,
    generate_id,
)
from src.core.types import PersonNameVisibility

# ---------------------------------------------------------------------------
# generate_id
# ---------------------------------------------------------------------------


def test_generate_id_returns_string():
    assert isinstance(generate_id(), str)


def test_generate_id_is_26_chars():
    """ULIDs are 26 characters in Crockford base32."""
    assert len(generate_id()) == 26


def test_generate_id_unique():
    ids = {generate_id() for _ in range(100)}
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


def test_get_pool_raises_when_not_initialised():
    """get_pool must raise RuntimeError when _pool is None (pool not created)."""
    original = db_module._pool
    db_module._pool = None
    try:
        with pytest.raises(RuntimeError, match="not initialised"):
            db_module.get_pool()
    finally:
        db_module._pool = original


def _ready_pool(conn):
    """Mock pool whose acquire/release drive ``check_ready`` (#343)."""
    pool = MagicMock()
    pool.acquire = AsyncMock(return_value=conn)
    pool.release = AsyncMock()
    return pool


async def test_check_ready_acquires_with_timeout_and_selects_one():
    """check_ready must bound acquire — a bare acquire on an exhausted pool
    hangs forever, making pool exhaustion indistinguishable from process death."""
    conn = AsyncMock()
    pool = _ready_pool(conn)
    with patch.object(db_module, "_pool", pool):
        await db_module.check_ready()
    (_, kwargs) = pool.acquire.await_args
    assert kwargs.get("timeout", 0) > 0
    conn.fetchval.assert_awaited_once_with("SELECT 1")
    pool.release.assert_awaited_once_with(conn)


async def test_check_ready_releases_connection_on_query_failure():
    """A failing SELECT must not leak the acquired connection."""
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.PostgresError("boom")
    pool = _ready_pool(conn)
    with patch.object(db_module, "_pool", pool), pytest.raises(asyncpg.PostgresError):
        await db_module.check_ready()
    pool.release.assert_awaited_once_with(conn)


async def test_check_ready_raises_without_pool():
    """No pool initialised → RuntimeError (the route maps it to 503 no_pool)."""
    with (
        patch.object(db_module, "_pool", None),
        pytest.raises(RuntimeError, match="not initialised"),
    ):
        await db_module.check_ready()


@pytest.mark.integration
async def test_check_ready_against_real_pool(db_pool):
    """End-to-end: check_ready completes against a live asyncpg pool.

    Guards the real ``Pool.acquire(timeout=...)`` call signature, which the
    mocked tests cannot.
    """
    with patch.object(db_module, "_pool", db_pool):
        await db_module.check_ready()


def test_generate_id_timestamp_nondecreasing():
    """The 10-char timestamp prefix of sequential ULIDs must be non-decreasing.

    The full ULID (timestamp + random) is not guaranteed to be in ascending
    order within the same millisecond, because the random component is
    independently generated each call. Pinning to the timestamp portion
    tests the only guarantee the spec makes: that time moves forward.
    """
    ids = [generate_id() for _ in range(10)]
    timestamps = [uid[:10] for uid in ids]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# _warn_if_lookup_tables_unseeded — empty/seeded behaviour
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db_conn():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@pytest.mark.integration
async def test_warn_fires_when_bcp47_locales_empty(db_conn, caplog):
    # Clear person_names.locale FK references before truncating bcp47_locales,
    # so the DELETE doesn't trip person_names_locale_fkey once Phase 2b starts
    # populating locale values.
    await db_conn.execute("UPDATE person_names SET locale = NULL")
    await db_conn.execute("DELETE FROM bcp47_locales")
    with caplog.at_level(logging.WARNING, logger="src.core.db"):
        await _warn_if_lookup_tables_unseeded(db_conn)
    matched = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "bcp47_locales" in r.getMessage()
    ]
    assert matched, "expected WARNING for empty bcp47_locales"


@pytest.mark.integration
async def test_warn_fires_when_iso15924_scripts_empty(db_conn, caplog):
    # bcp47_locales.script FK references iso15924_scripts, so clear locales first
    # to avoid blocking the scripts DELETE.
    await db_conn.execute("DELETE FROM person_names")
    await db_conn.execute("DELETE FROM bcp47_locales")
    await db_conn.execute("DELETE FROM iso15924_scripts")
    with caplog.at_level(logging.WARNING, logger="src.core.db"):
        await _warn_if_lookup_tables_unseeded(db_conn)
    matched = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "iso15924_scripts" in r.getMessage()
    ]
    assert matched, "expected WARNING for empty iso15924_scripts"


@pytest.mark.integration
async def test_person_name_visibility_literal_matches_db_check(db_conn):
    """`PersonNameVisibility` must enumerate every value the DB CHECK accepts.

    Drift here means either the Literal silently rejects a tier the DB still
    permits (admin form returns 422 on a valid value), or the DB rejects a
    tier the form happily accepts (handler 500). Either way the contract is
    broken — fail loudly here instead.
    """
    constraint_def = await db_conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'person_names_visibility_check'"
    )
    assert constraint_def, "person_names_visibility_check constraint missing"
    db_values = set(re.findall(r"'([^']+)'", constraint_def))
    literal_values = set(get_args(PersonNameVisibility))
    assert db_values == literal_values, (
        f"DB CHECK and PersonNameVisibility Literal disagree:\n"
        f"  DB only:      {db_values - literal_values}\n"
        f"  Literal only: {literal_values - db_values}"
    )


@pytest.mark.integration
async def test_warn_silent_when_both_lookup_tables_seeded(db_conn, caplog):
    """Pre-seeded prod state: no warning."""
    bcp_n = await db_conn.fetchval("SELECT COUNT(*) FROM bcp47_locales")
    iso_n = await db_conn.fetchval("SELECT COUNT(*) FROM iso15924_scripts")
    if bcp_n == 0 or iso_n == 0:
        pytest.skip("lookup tables not seeded — run scripts/seed_locales_scripts.py")
    with caplog.at_level(logging.WARNING, logger="src.core.db"):
        await _warn_if_lookup_tables_unseeded(db_conn)
    lookup_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and ("bcp47_locales" in r.getMessage() or "iso15924_scripts" in r.getMessage())
    ]
    assert not lookup_warnings, f"unexpected warning(s): {lookup_warnings}"


# ---------------------------------------------------------------------------
# _parse_schema_statements — unit tests
# ---------------------------------------------------------------------------


def test_parse_schema_statements_basic():
    sql = "CREATE TABLE foo (id INT);\nCREATE INDEX idx_foo ON foo (id);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2
    assert "CREATE TABLE" in stmts[0]
    assert "CREATE INDEX" in stmts[1]


def test_parse_schema_statements_dollar_quote():
    """Semicolons inside dollar-quoted bodies must not split the statement."""
    sql = "DO $$ BEGIN RAISE NOTICE 'hi;there'; END $$;\nCREATE TABLE t (id INT);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("DO $$")
    assert "CREATE TABLE" in stmts[1]


def test_parse_schema_statements_skips_empty():
    sql = "CREATE TABLE t (id INT);;\n  ;\nCREATE INDEX idx ON t (id);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2


def test_parse_schema_statements_semicolon_in_line_comment():
    """A semicolon inside a -- comment must not split the statement."""
    sql = "-- slug is unique; see docs\nCREATE TABLE t (id INT);\nCREATE INDEX idx ON t (id);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2
    assert "CREATE TABLE" in stmts[0]
    assert "CREATE INDEX" in stmts[1]


def test_parse_schema_statements_semicolon_in_string_literal():
    """A semicolon inside a single-quoted string must not split the statement."""
    sql = "INSERT INTO t (v) VALUES ('a;b');\nCREATE INDEX idx ON t (v);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2
    assert "INSERT" in stmts[0]


def test_parse_schema_statements_named_dollar_tag():
    sql = "DO $body$ BEGIN NULL; END $body$;\nCREATE TABLE t2 (id INT);"
    stmts = _parse_schema_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("DO $body$")


# ---------------------------------------------------------------------------
# _is_non_unique_create_index — unit tests
# ---------------------------------------------------------------------------


def test_is_non_unique_create_index_plain():
    assert _is_non_unique_create_index("CREATE INDEX idx_foo ON t (c)")


def test_is_non_unique_create_index_if_not_exists():
    assert _is_non_unique_create_index("CREATE INDEX IF NOT EXISTS idx_foo ON t (c)")


def test_is_non_unique_create_index_rejects_unique():
    assert not _is_non_unique_create_index("CREATE UNIQUE INDEX uq_foo ON t (c)")


def test_is_non_unique_create_index_rejects_table():
    assert not _is_non_unique_create_index("CREATE TABLE foo (id INT)")


def test_is_non_unique_create_index_rejects_trigger():
    assert not _is_non_unique_create_index("CREATE TRIGGER trg AFTER INSERT ON t ...")


def test_is_non_unique_create_index_case_insensitive():
    assert _is_non_unique_create_index("create index idx_lower ON t (c)")


def test_is_non_unique_create_index_already_concurrently():
    """CREATE INDEX CONCURRENTLY is still a non-unique index — must route to Phase 2."""
    assert _is_non_unique_create_index("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx ON t (c)")


# ---------------------------------------------------------------------------
# apply_schema — concurrent index behaviour (unit, via mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_schema_sends_concurrently_for_non_unique_indexes():
    """Non-unique CREATE INDEX statements must be rewritten with CONCURRENTLY.

    Phase 1 is one batched execute call (transactional DDL joined).
    Phase 2 calls are one-per-index, each containing CONCURRENTLY.
    """
    sql = (
        "CREATE TABLE t (id INT);\n"
        "CREATE UNIQUE INDEX uq_t ON t (id);\n"
        "CREATE INDEX IF NOT EXISTS idx_t ON t (id);"
    )
    captured: list[str] = []

    class FakeTx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    mock_conn = MagicMock()
    mock_conn.transaction.return_value = FakeTx()
    mock_conn.execute = AsyncMock(side_effect=lambda stmt: captured.append(stmt))
    mock_conn.fetchval = AsyncMock(return_value=1)  # lookup tables "seeded"
    mock_conn.is_in_transaction.return_value = False

    with patch.object(db_module, "SCHEMA_PATH") as mock_path:
        mock_path.read_text.return_value = sql
        await apply_schema(mock_conn)

    # Phase 2 execute calls: individual statements, each CREATE INDEX CONCURRENTLY
    # Phase 1 is a single batch call (joined string); find phase-2 calls by being
    # shorter (one statement each, not containing newlines joining multiple stmts).
    # Simpler: any call that is purely a single CREATE INDEX statement.
    phase2_calls = [
        s for s in captured if re.match(r"\s*CREATE INDEX CONCURRENTLY\b", s, re.IGNORECASE)
    ]
    assert phase2_calls, "expected at least one CREATE INDEX CONCURRENTLY call in phase 2"

    # UNIQUE INDEX must not appear in any phase-2 call
    for stmt in phase2_calls:
        assert "UNIQUE" not in stmt.upper(), f"UNIQUE INDEX must not appear in phase-2: {stmt!r}"


@pytest.mark.asyncio
async def test_apply_schema_unique_index_stays_in_transaction():
    """UNIQUE INDEX stays in the transactional batch; non-unique goes CONCURRENTLY after."""
    sql = (
        "CREATE TABLE t (id INT);\n"
        "CREATE UNIQUE INDEX uq_t ON t (id);\n"
        "CREATE INDEX idx_t ON t (id);"
    )
    phase1_sql: list[str] = []
    phase2_calls: list[str] = []
    in_transaction = False

    class FakeTx:
        async def __aenter__(self):
            nonlocal in_transaction
            in_transaction = True
            return self

        async def __aexit__(self, *_):
            nonlocal in_transaction
            in_transaction = False

    async def capture_execute(stmt: str) -> None:
        if in_transaction:
            phase1_sql.append(stmt)
        else:
            phase2_calls.append(stmt)

    mock_conn = MagicMock()
    mock_conn.transaction.return_value = FakeTx()
    mock_conn.execute = AsyncMock(side_effect=capture_execute)
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.is_in_transaction.return_value = False

    with patch.object(db_module, "SCHEMA_PATH") as mock_path:
        mock_path.read_text.return_value = sql
        await apply_schema(mock_conn)

    # Phase 1: one batched call containing the UNIQUE INDEX
    assert len(phase1_sql) == 1, "Phase 1 must be a single batched execute call"
    assert "UNIQUE" in phase1_sql[0].upper(), "UNIQUE INDEX must be in the phase-1 batch"

    # Phase 2: non-unique index, with CONCURRENTLY, no UNIQUE
    assert phase2_calls, "expected phase-2 calls for non-unique CREATE INDEX"
    assert not any("UNIQUE" in s.upper() for s in phase2_calls), (
        "UNIQUE INDEX must not appear in phase-2"
    )
    assert all("CONCURRENTLY" in s.upper() for s in phase2_calls), (
        "all phase-2 CREATE INDEX calls must contain CONCURRENTLY"
    )


@pytest.mark.asyncio
async def test_apply_schema_falls_back_when_inside_transaction():
    """When conn is already in a transaction, Phase 2 uses plain CREATE INDEX.

    Covers both plain CREATE INDEX (must stay plain) and CREATE INDEX CONCURRENTLY
    already in the source (must have CONCURRENTLY stripped — finding #4 guard).
    """
    sql = (
        "CREATE TABLE t (id INT);\n"
        "CREATE INDEX idx_t ON t (id);\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_t2 ON t (id);"
    )
    phase2_calls: list[str] = []
    in_transaction = False

    class FakeTx:
        async def __aenter__(self):
            nonlocal in_transaction
            in_transaction = True
            return self

        async def __aexit__(self, *_):
            nonlocal in_transaction
            in_transaction = False

    async def capture_execute(stmt: str) -> None:
        if not in_transaction:
            phase2_calls.append(stmt)

    mock_conn = MagicMock()
    mock_conn.transaction.return_value = FakeTx()
    mock_conn.execute = AsyncMock(side_effect=capture_execute)
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.is_in_transaction.return_value = True  # simulate pre-existing outer transaction

    with patch.object(db_module, "SCHEMA_PATH") as mock_path:
        mock_path.read_text.return_value = sql
        await apply_schema(mock_conn)

    assert len(phase2_calls) == 2, f"expected 2 phase-2 calls, got {phase2_calls}"
    for stmt in phase2_calls:
        assert "CONCURRENTLY" not in stmt.upper(), (
            f"must not use CONCURRENTLY inside a transaction: {stmt!r}"
        )


@pytest.mark.asyncio
async def test_apply_schema_no_concurrently_doubling():
    """Phase 2 must not produce CREATE INDEX CONCURRENTLY CONCURRENTLY.

    Guards against re.sub doubling if schema.sql ever contains CREATE INDEX CONCURRENTLY
    directly (finding #1 guard).
    """
    sql = "CREATE TABLE t (id INT);\nCREATE INDEX CONCURRENTLY IF NOT EXISTS idx_t ON t (id);"
    captured: list[str] = []

    class FakeTx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    mock_conn = MagicMock()
    mock_conn.transaction.return_value = FakeTx()
    mock_conn.execute = AsyncMock(side_effect=lambda stmt: captured.append(stmt))
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_conn.is_in_transaction.return_value = False

    with patch.object(db_module, "SCHEMA_PATH") as mock_path:
        mock_path.read_text.return_value = sql
        await apply_schema(mock_conn)

    phase2_calls = [s for s in captured if "CONCURRENTLY" in s.upper()]
    assert phase2_calls, "expected at least one CONCURRENTLY call"
    for stmt in phase2_calls:
        upper = stmt.upper()
        first = upper.find("CONCURRENTLY")
        second = upper.find("CONCURRENTLY", first + 1)
        assert second == -1, f"CONCURRENTLY appears twice in phase-2 stmt: {stmt!r}"


# ---------------------------------------------------------------------------
# apply_schema — concurrent index on non-empty table (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_apply_schema_concurrent_index_on_non_empty_table():
    """CONCURRENTLY index build succeeds with existing rows and is idempotent."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")

    # Raw connection required: CONCURRENTLY cannot run inside a transaction, so the
    # db_conn session fixture (which wraps in a rollback transaction) cannot be used.
    conn = await asyncpg.connect(dsn)
    try:
        # Scratch table + index — isolated from schema.sql to avoid side effects
        await conn.execute("DROP TABLE IF EXISTS _test_concurrent_idx_target")
        await conn.execute(
            "CREATE TABLE _test_concurrent_idx_target (id SERIAL PRIMARY KEY, val INT)"
        )
        await conn.execute(
            "INSERT INTO _test_concurrent_idx_target (val) SELECT g FROM generate_series(1, 100) g"
        )

        # Simulate the concurrent-index phase directly
        await conn.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS _test_concurrent_idx"
            " ON _test_concurrent_idx_target (val)"
        )

        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = '_test_concurrent_idx')"
        )
        assert exists, "index not found in pg_indexes after CONCURRENTLY build"

        # Idempotency — second call must not raise
        await conn.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS _test_concurrent_idx"
            " ON _test_concurrent_idx_target (val)"
        )
    finally:
        await conn.execute("DROP TABLE IF EXISTS _test_concurrent_idx_target")
        await conn.close()
