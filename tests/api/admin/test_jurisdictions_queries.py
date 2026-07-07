"""Integration tests for the jurisdictions admin list query builder (#275).

Mirrors the orgs_queries pattern. Seeds active / archived / superseded rows under
a unique search marker so assertions are isolated from any pre-seeded data.
"""

import pytest
import pytest_asyncio

from src.api.admin.jurisdictions_queries import query_jurisdictions_rows
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Per-test connection acquired from the session-scoped pool."""
    async with db_pool.acquire() as conn:
        yield conn


@pytest_asyncio.fixture(loop_scope="session")
async def sample_jurisdictions(db):
    """Seed active (county), archived (city), and superseded (county) rows.

    Superseded rows keep ``archived_at IS NULL`` (supersession is not soft-delete),
    so the three statuses partition cleanly on (archived_at, superseded_at).
    """
    marker = generate_id()[-10:].lower()
    county_type = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    city_type = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='city'")
    ids = {
        "active": generate_id(),
        "archived": generate_id(),
        "superseded": generate_id(),
    }
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        ids["active"],
        f"test-{marker}-active",
        f"Testville {marker} Active",
        county_type,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
        " VALUES ($1,$2,$3,$4, NOW())",
        ids["archived"],
        f"test-{marker}-archived",
        f"Testville {marker} Archived",
        city_type,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, superseded_at)"
        " VALUES ($1,$2,$3,$4, NOW())",
        ids["superseded"],
        f"test-{marker}-superseded",
        f"Testville {marker} Superseded",
        county_type,
    )
    yield {"marker": marker, "ids": ids}
    await db.execute("DELETE FROM jurisdictions WHERE id = ANY($1::text[])", list(ids.values()))


async def test_active_excludes_archived_and_superseded(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, count, _ = await query_jurisdictions_rows(
        db, q=marker, status="active", type_slug=None, page=1, page_size=50
    )
    slugs = {r["slug"] for r in rows}
    assert f"test-{marker}-active" in slugs
    assert f"test-{marker}-archived" not in slugs
    assert f"test-{marker}-superseded" not in slugs
    assert count == 1


async def test_archived_status(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, _, _ = await query_jurisdictions_rows(
        db, q=marker, status="archived", type_slug=None, page=1, page_size=50
    )
    assert {r["slug"] for r in rows} == {f"test-{marker}-archived"}


async def test_superseded_status(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, _, _ = await query_jurisdictions_rows(
        db, q=marker, status="superseded", type_slug=None, page=1, page_size=50
    )
    assert {r["slug"] for r in rows} == {f"test-{marker}-superseded"}


async def test_type_filter(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, _, _ = await query_jurisdictions_rows(
        db, q=marker, status="active", type_slug="county", page=1, page_size=50
    )
    assert {r["slug"] for r in rows} == {f"test-{marker}-active"}
    rows2, _, _ = await query_jurisdictions_rows(
        db, q=marker, status="active", type_slug="city", page=1, page_size=50
    )
    assert rows2 == []


async def test_search_matches_name(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, _, _ = await query_jurisdictions_rows(
        db, q=f"Testville {marker} Active", status="active", type_slug=None, page=1, page_size=50
    )
    assert {r["slug"] for r in rows} == {f"test-{marker}-active"}


async def test_rows_expose_type_display(db, sample_jurisdictions):
    marker = sample_jurisdictions["marker"]
    rows, _, pctx = await query_jurisdictions_rows(
        db, q=marker, status="active", type_slug=None, page=1, page_size=50
    )
    assert rows[0]["type_slug"] == "county"
    assert rows[0]["type_display_name"] == "County"
    assert pctx["page"] == 1
