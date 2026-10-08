"""Integration tests for person identifiers CRUD (parity with test_orgs_identifiers.py)."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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
async def person_id_and_type(db):
    """Yields (person_id, identifier_type_id) for a person with an identifier type seeded."""
    pid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)

    row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='person' AND NOT is_internal ORDER BY slug LIMIT 1"
    )
    if not row:
        pytest.skip("No person identifier types seeded")
    type_id = row["id"]

    yield pid, type_id


@pytest_asyncio.fixture(loop_scope="session")
async def person_and_identifier(db, person_id_and_type):
    pid, type_id = person_id_and_type
    iid = generate_id()

    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, 'TEST-123')",
        iid,
        pid,
        type_id,
    )

    yield pid, iid, type_id


async def test_identifier_form_row_has_form_group(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = await client.get(f"/admin/people/{pid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_identifiers_new_row_returns_form(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = await client.get(f"/admin/people/{pid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_create(client, person_id_and_type):
    pid, type_id = person_id_and_type
    r = await client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-999"},
    )
    assert r.status_code == 200
    assert "UBI-999" in r.text


async def test_identifiers_read_row_returns_row(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = await client.get(f"/admin/people/{pid}/identifiers/{iid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "TEST-123" in r.text
    assert "<form" not in r.text


async def test_identifiers_edit_row_returns_form(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = await client.get(f"/admin/people/{pid}/identifiers/{iid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_update(client, person_and_identifier):
    pid, iid, type_id = person_and_identifier
    r = await client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-456"},
    )
    assert r.status_code == 200
    assert "UBI-456" in r.text


async def test_identifiers_delete(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = await client.delete(f"/admin/people/{pid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_identifiers_delete_unknown_returns_404(client, person_id_and_type):
    pid, _ = person_id_and_type
    r = await client.delete(
        f"/admin/people/{pid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


async def test_identifiers_create_returns_success_flash(client, person_id_and_type):
    pid, type_id = person_id_and_type
    r = await client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-001"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-001" in trigger["showFlash"]["body"]


async def test_identifiers_update_returns_success_flash(client, person_and_identifier):
    pid, iid, type_id = person_and_identifier
    r = await client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-002"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-002" in trigger["showFlash"]["body"]


async def test_identifiers_delete_returns_info_flash(client, person_and_identifier):
    pid, iid, _ = person_and_identifier
    r = await client.delete(f"/admin/people/{pid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# #617 CR — the form's type must belong to this entity type and be external
# ---------------------------------------------------------------------------


async def _type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)


@pytest.mark.parametrize("slug", ["org_ubi", "pm_person_id"])
async def test_identifiers_create_refuses_foreign_or_internal_type(
    client, db, person_id_and_type, slug
):
    """A posted type the picker never offers is refused before any write.

    get_db opens no transaction, so a write followed by the read-back 404 left
    the row behind (#617 CR): an org_ubi on a person, or an internal pm_* type.
    """
    pid, _ = person_id_and_type
    r = await client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, slug), "value": "CR617"},
    )
    assert r.status_code == 400
    assert await db.fetchval("SELECT count(*) FROM identifiers WHERE entity_id=$1", pid) == 0


@pytest.mark.parametrize("slug", ["org_ubi", "pm_person_id"])
async def test_identifiers_update_refuses_foreign_or_internal_type(
    client, db, person_and_identifier, slug
):
    """An edit cannot retype a person's identifier to another entity's or a pm_* type."""
    pid, iid, type_id = person_and_identifier
    r = await client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, slug), "value": "CR617"},
    )
    assert r.status_code == 400
    row = await db.fetchrow(
        "SELECT entity_identifier_type_id, value FROM identifiers WHERE id=$1", iid
    )
    assert (row["entity_identifier_type_id"], row["value"]) == (type_id, "TEST-123")


# --- #622: the person vanishes between the route's check and its write ---


async def _vanish_as_the_write_begins(db, pid: str) -> None:
    """Delete ``pid`` from inside the identifier write, after the route's check.

    A ``BEFORE`` trigger that sorts ahead of ``trg_identifiers_entity`` stands in
    for a hard delete committing in between; both roll back with the test.
    """
    await db.execute(
        "CREATE FUNCTION pg_temp.t622_vanish() RETURNS TRIGGER LANGUAGE plpgsql AS"
        f" $$ BEGIN DELETE FROM people WHERE id = '{pid}'; RETURN NEW; END $$"
    )
    await db.execute(
        "CREATE TRIGGER a_t622_vanish BEFORE INSERT OR UPDATE ON identifiers"
        " FOR EACH ROW EXECUTE FUNCTION pg_temp.t622_vanish()"
    )


async def test_identifiers_create_on_a_vanished_person_is_404(client, db, person_id_and_type):
    """The schema guard (#622) refuses the write; the curator gets the 404, not a 500."""
    pid, type_id = person_id_and_type
    await _vanish_as_the_write_begins(db, pid)

    r = await client.post(
        f"/admin/people/{pid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "CR622"},
    )

    assert r.status_code == 404, r.text
    assert await db.fetchval("SELECT count(*) FROM identifiers WHERE entity_id=$1", pid) == 0


async def test_identifiers_update_on_a_vanished_person_is_404(client, db, person_and_identifier):
    """Retyping rechecks the entity; a vanished one is the 404, and the row is unchanged."""
    pid, iid, type_id = person_and_identifier
    await _vanish_as_the_write_begins(db, pid)

    r = await client.post(
        f"/admin/people/{pid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, "person_wa_pdc"), "value": "CR622"},
    )

    assert r.status_code == 404, r.text
    row = await db.fetchrow(
        "SELECT entity_identifier_type_id, value FROM identifiers WHERE id=$1", iid
    )
    assert (row["entity_identifier_type_id"], row["value"]) == (type_id, "TEST-123")
