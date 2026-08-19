"""Integration tests for admin dashboard route."""

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.conftest import ENTITY_ORDER_HREFS, assert_render_order

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_counts(db):
    """Insert one active record per entity. Rolled back with the test transaction."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    assignment_id = generate_id()
    batch_id = generate_id()
    jurisdiction_id = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    jur_type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
        jurisdiction_id,
        f"test-dash-{jurisdiction_id[-8:].lower()}",
        "Test County",
        jur_type_id,
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        role_id,
        org_id,
        "Test Role",
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        assignment_id,
        person_id,
        role_id,
    )
    await db.execute(
        "INSERT INTO import_batches"
        " (id, source_file, file_hash, row_count, loaded_count, error_count)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        batch_id,
        "test.csv",
        "testhash_dashboard",
        0,
        0,
        0,
    )


async def test_dashboard_shows_counts(client, seeded_counts):
    """Entity, settings, and activity cards display numeric record counts."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "— records" not in resp.text
    counts = re.findall(r"(\d+) records", resp.text)
    # Jurisdictions, Organizations, People, Roles, Assignments, Import History
    assert len(counts) == 6, f"Expected 6 count boxes, found: {counts}"


async def test_dashboard_entity_cards_jurisdiction_first(client):
    """Dashboard entity cards are ordered Jurisdiction, Org, Person, Role,
    Assignment (#275). Entity list hrefs appear only in the card grid within the
    <main> region, so first-occurrence position there reflects card order."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    content = resp.text.split('id="main-content"')[1]
    assert_render_order(content, ENTITY_ORDER_HREFS)


async def test_dashboard_has_api_activity_panel(client):
    """Dashboard surfaces the API Activity (24h) panel linking to the request log (#260)."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "API Activity" in resp.text
    assert 'href="/admin/activity/requests/"' in resp.text


async def test_dashboard_activity_cards_api_activity_first(client):
    """Activity cards render API Activity before Import History. Both headings
    appear only inside the card grid within the <main> region, so
    first-occurrence position there reflects card order."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    content = resp.text.split('id="main-content"')[1]
    assert_render_order(content, [">API Activity<", ">Import History<"])


async def test_dashboard_has_org_dup_badge_slot(client):
    """Org dup badge loaded async; dashboard must contain the HTMX slot, not inline count."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert 'hx-get="/admin/_dup-badge/orgs/?variant=card"' in resp.text
    assert 'hx-swap="innerHTML"' in resp.text
    assert "org_dup_count" not in resp.text


async def test_dashboard_has_person_dup_badge_slot(client):
    """Person dup badge loaded async; dashboard must contain the HTMX slot, not inline count."""
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert 'hx-get="/admin/_dup-badge/people/?variant=card"' in resp.text
    assert 'hx-swap="innerHTML"' in resp.text
    assert "person_dup_count" not in resp.text


async def test_dashboard_routes_db_through_get_db_dep(client, db):
    """Dashboard's DB connection must come from Depends(get_db) so test overrides apply (#147)."""
    call_count = {"n": 0}

    async def counting_get_db():
        call_count["n"] += 1
        yield db

    app.dependency_overrides[get_db] = counting_get_db
    resp = await client.get("/admin/", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert call_count["n"] >= 1, "dashboard route did not route DB acquisition through get_db dep"
