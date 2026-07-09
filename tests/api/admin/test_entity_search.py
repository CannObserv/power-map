"""Integration tests for the unified entity-search typeahead endpoint.

Backs the linked-entity typeahead on the admin event form (#172): one endpoint
that dispatches to people or organizations based on ``linked_entity_type``.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "test-user", "X-ExeDev-Email": "test@example.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_person(db, name: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def test_search_person_scope_returns_person(client, db):
    pid = await _make_person(db, "Jane Linkable")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Jane", "linked_entity_type": "person"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "Jane Linkable" in r.text
    assert f'data-id="{pid}"' in r.text


async def test_search_org_scope_returns_org(client, db):
    oid = await _make_org(db, "Acme Linkable Corp")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Acme", "linked_entity_type": "organization"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "Acme Linkable Corp" in r.text
    assert f'data-id="{oid}"' in r.text


async def test_search_person_scope_excludes_orgs(client, db):
    await _make_org(db, "Shared Token Org")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Shared Token", "linked_entity_type": "person"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "Shared Token Org" not in r.text
    assert "data-id" not in r.text


async def test_search_empty_query_returns_empty(client, db):
    await _make_person(db, "Nobody Searches")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "", "linked_entity_type": "person"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "data-id" not in r.text


async def test_search_unknown_type_returns_empty(client, db):
    await _make_person(db, "Typeless Target")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Typeless", "linked_entity_type": "role"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "data-id" not in r.text


async def test_search_missing_type_returns_empty(client, db):
    await _make_person(db, "Untyped Target")
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Untyped"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "data-id" not in r.text


async def test_search_excludes_archived(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Archived Linkable', TRUE)",
        generate_id(),
        oid,
    )
    r = await client.get(
        "/admin/entities/search/",
        params={"q": "Archived Linkable", "linked_entity_type": "organization"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "Archived Linkable" not in r.text
