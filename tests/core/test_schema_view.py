"""Integration tests for v_org_display_names view."""

import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    c = await asyncpg.connect(dsn)
    await apply_schema(c)
    yield c
    await c.close()


async def _insert_org(conn, name, acronym=None):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(), oid, name,
    )
    if acronym:
        await conn.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(), oid, acronym,
        )
    return oid


async def test_view_formats_name_with_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert row["display_name"] == "National Cannabis Industry Association (NCIA)"
    finally:
        await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)


async def test_view_shows_name_only_when_no_acronym(conn):
    oid = await _insert_org(conn, "Small Local Org")
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert row["display_name"] == "Small Local Org"
    finally:
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)


async def test_view_returns_one_row_per_org_with_both_name_and_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    try:
        rows = await conn.fetch(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert len(rows) == 1
    finally:
        await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)


async def test_view_shows_acronym_only_when_no_name(conn):
    """Org with only a canonical acronym must show the acronym as display_name."""
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'ACME', TRUE)",
        generate_id(), oid,
    )
    try:
        row = await conn.fetchrow(
            "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
        )
        assert row["display_name"] == "ACME"
    finally:
        await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
        await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
