"""Admin ancillary CRUD must emit a parent ``entity_changes`` 'updated' row (#327).

These are outcome tests: an admin create/update/delete of ``contact_methods`` /
``links`` / ``identifiers`` must signal the parent, regardless of mechanism. #327
makes that a DB touch-cascade trigger on ``contact_methods`` / ``links`` (all
entity types) plus a ``role_assignment`` branch on the existing identifier
trigger — the same model as ``entity_addresses`` — so every write path (admin,
public observation, merge) signals uniformly and no admin-only app-layer emit is
needed. Exercising a representative set of parent entity types per ancillary is
sufficient; jurisdiction rides the identical polymorphic trigger as organization.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
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


# ── helpers ──────────────────────────────────────────────────────────────────


async def _make_entity(db, kind: str) -> tuple[str, str, str]:
    """Create a parent entity of ``kind``; return (entity_id, entity_type, url_seg).

    ``entity_type`` is the value stored in the polymorphic ancillary table;
    ``url_seg`` is the admin URL segment for that entity's ancillary routers.
    """
    if kind == "organization":
        oid = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        return oid, "organization", f"/admin/orgs/{oid}"
    if kind == "person":
        pid = generate_id()
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
        return pid, "person", f"/admin/people/{pid}"
    if kind == "role":
        oid, rid = generate_id(), generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
        )
        return rid, "role", f"/admin/roles/{rid}"
    if kind == "role_assignment":
        oid, rid, pid, raid = (generate_id() for _ in range(4))
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
        )
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
            " VALUES ($1, $2, $3, TRUE)",
            raid,
            pid,
            rid,
        )
        return raid, "role_assignment", f"/admin/role-assignments/{raid}"
    raise ValueError(kind)


async def _change_count(db, entity_type: str, entity_id: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type=$1 AND entity_id=$2 AND change_kind='updated'",
        entity_type,
        entity_id,
    )


# ── contacts ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["organization", "person", "role", "role_assignment"])
async def test_contacts_crud_emits_entity_change(client, db, kind):
    entity_id, entity_type, url = await _make_entity(db, kind)

    # create
    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "a@example.gov"},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    cid = await db.fetchval(
        "SELECT id FROM contact_methods WHERE entity_type=$1 AND entity_id=$2",
        entity_type,
        entity_id,
    )

    # update
    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "b@example.gov"},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    # delete
    before = await _change_count(db, entity_type, entity_id)
    r = await client.delete(f"{url}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1


# ── links ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["organization", "person", "role", "role_assignment"])
async def test_links_crud_emits_entity_change(client, db, kind):
    entity_id, entity_type, url = await _make_entity(db, kind)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")

    # create
    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.gov/a", "link_type_id": lt_id},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    lid = await db.fetchval(
        "SELECT id FROM links WHERE entity_type=$1 AND entity_id=$2", entity_type, entity_id
    )

    # update
    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/links/{lid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.gov/b", "link_type_id": lt_id},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    # delete
    before = await _change_count(db, entity_type, entity_id)
    r = await client.delete(f"{url}/links/{lid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1


# ── identifiers ──────────────────────────────────────────────────────────────


async def test_identifiers_role_assignment_crud_emits_entity_change(client, db):
    """role_assignment is the identifier scope the touch trigger gained in #327."""
    entity_id, entity_type, url = await _make_entity(db, "role_assignment")
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='role_assignment' AND slug='role_wa_pdc'"
    )

    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "12345"},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    iid = await db.fetchval("SELECT id FROM identifiers WHERE entity_id=$1", entity_id)

    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "67890"},
    )
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1

    before = await _change_count(db, entity_type, entity_id)
    r = await client.delete(f"{url}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert await _change_count(db, entity_type, entity_id) == before + 1


async def test_identifiers_org_emits_exactly_once_no_double(client, db):
    """organization identifiers were already trigger-covered — a single mutation
    emits exactly once, not twice (guards against a stray second app-layer emit)."""
    entity_id, entity_type, url = await _make_entity(db, "organization")
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type='organization' LIMIT 1"
    )

    before = await _change_count(db, entity_type, entity_id)
    r = await client.post(
        f"{url}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "ORG-1"},
    )
    assert r.status_code == 200
    # Exactly one signal (from the trigger), not two.
    assert await _change_count(db, entity_type, entity_id) == before + 1
