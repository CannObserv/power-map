"""Org merge still deletes a loser that another org's event links to (#615).

``trg_organizations_inbound_event_links`` refuses a DELETE while any other
entity's event links the org. The merge passes only because
``rehome_entity_events`` re-points those links onto the winner first (#611);
this drives the admin route through its real ``DELETE FROM organizations``.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

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
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _mk_org(db, name: str) -> str:
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


async def test_merge_repoints_an_inbound_link_then_deletes_the_loser(client, db):
    winner, loser = await _mk_org(db, "Inbound Winner"), await _mk_org(db, "Inbound Loser")
    predecessor = await _mk_org(db, "Inbound Predecessor")
    event_id = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)"
        " SELECT $1, 'organization', $2, t.id, 'organization', $3"
        " FROM entity_event_types t WHERE t.slug = 'succeeded_by'",
        event_id,
        predecessor,
        loser,
    )

    r = await client.post(f"/admin/orgs/{winner}/merge-with/{loser}/", headers=AUTH_HEADERS)

    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/admin/orgs/{winner}/?flash=saved"
    assert not await db.fetchval("SELECT 1 FROM organizations WHERE id = $1", loser)
    assert (
        await db.fetchval("SELECT linked_entity_id FROM entity_events WHERE id = $1", event_id)
        == winner
    )
