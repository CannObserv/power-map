"""Continuity banner on org detail (#469).

An org sitting in a succession chain announces it: the predecessor links
forward to its successor, the successor links back. An unchained org shows
nothing.
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

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _mk_org(db, name):
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


@pytest_asyncio.fixture(loop_scope="session")
async def chain(db):
    pred = await _mk_org(db, "Banner Predecessor Committee")
    succ = await _mk_org(db, "Banner Successor Committee")
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, event_year,
                linked_entity_type, linked_entity_id)
           SELECT $1, 'organization', $2, t.id, 2020, 'organization', $3
           FROM entity_event_types t WHERE t.slug = 'succeeded_by'""",
        generate_id(),
        pred,
        succ,
    )
    return pred, succ


async def test_predecessor_banner_links_forward(client, chain):
    pred, succ = chain
    r = await client.get(f"/admin/orgs/{pred}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "org-succession-banner" in r.text
    assert "Succeeded by" in r.text
    assert "Banner Successor Committee" in r.text
    assert f'href="/admin/orgs/{succ}/"' in r.text


async def test_successor_banner_links_back(client, chain):
    pred, succ = chain
    r = await client.get(f"/admin/orgs/{succ}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "org-succession-banner" in r.text
    assert "Continues" in r.text
    assert "Banner Predecessor Committee" in r.text
    assert f'href="/admin/orgs/{pred}/"' in r.text


async def test_unchained_org_shows_no_banner(client, db):
    oid = await _mk_org(db, "Banner Standalone Org")
    r = await client.get(f"/admin/orgs/{oid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "org-succession-banner" not in r.text
