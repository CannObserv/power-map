"""Tests for src.core.db — schema helpers and ULID generation."""

import logging
import os

import asyncpg
import pytest

import src.core.db as db_module
from src.core.db import _warn_if_lookup_tables_unseeded, apply_schema, generate_id

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


@pytest.fixture
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
    await db_conn.execute("DELETE FROM bcp47_locales")
    with caplog.at_level(logging.WARNING, logger="src.core.db"):
        await _warn_if_lookup_tables_unseeded(db_conn)
    matched = [
        r for r in caplog.records
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
        r for r in caplog.records
        if r.levelno == logging.WARNING and "iso15924_scripts" in r.getMessage()
    ]
    assert matched, "expected WARNING for empty iso15924_scripts"


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
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and ("bcp47_locales" in r.getMessage() or "iso15924_scripts" in r.getMessage())
    ]
    assert not lookup_warnings, f"unexpected warning(s): {lookup_warnings}"

