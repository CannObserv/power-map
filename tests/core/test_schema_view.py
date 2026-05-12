"""Integration tests for v_org_display_names view."""

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _insert_org(conn, name, acronym=None):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        oid,
        name,
    )
    if acronym:
        await conn.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            oid,
            acronym,
        )
    return oid


async def test_view_formats_name_with_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    row = await conn.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
    )
    assert row["display_name"] == "National Cannabis Industry Association (NCIA)"


async def test_view_shows_name_only_when_no_acronym(conn):
    oid = await _insert_org(conn, "Small Local Org")
    row = await conn.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
    )
    assert row["display_name"] == "Small Local Org"


async def test_view_returns_one_row_per_org_with_both_name_and_acronym(conn):
    oid = await _insert_org(conn, "National Cannabis Industry Association", "NCIA")
    rows = await conn.fetch(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
    )
    assert len(rows) == 1


async def test_view_shows_acronym_only_when_no_name(conn):
    """Org with only a canonical acronym must show the acronym as display_name."""
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'ACME', TRUE)",
        generate_id(),
        oid,
    )
    row = await conn.fetchrow(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", oid
    )
    assert row["display_name"] == "ACME"
