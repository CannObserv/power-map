"""#608: an admin hard delete refuses while another entity's event links to it.

``entity_events.linked_entity_id`` has no FK, so nothing stops a delete from
leaving, say, a predecessor's ``succeeded_by`` edge naming a deleted org. That
event belongs to the *other* entity, and its type may require the link, so the
delete is refused (409) — archived inbound events included, since unarchiving
one would restore the dangling link — exactly as a real FK reference refuses
it. The curator archives and deletes the inbound event first.
"""

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.db_utils import until_lock_waiting

pytestmark = [pytest.mark.integration]
HTMX_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
    "HX-Request": "true",
}

#: entity type → (admin URL segment, table, a linking event slug, its owner's type)
CASES = {
    "organization": ("orgs", "organizations", "succeeded_by", "organization"),
    "person": ("people", "people", "marriage", "person"),
}


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _entity(db, entity_type: str, *, archived: bool, name: str | None = None) -> str:
    eid = generate_id()
    table = CASES[entity_type][1]
    await db.execute(
        f"INSERT INTO {table} (id, archived_at) VALUES ($1, CASE WHEN $2 THEN now() END)",
        eid,
        archived,
    )
    if name and entity_type == "organization":
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            eid,
            name,
        )
    return eid


async def _inbound_event(db, entity_type: str, owner: str, target: str, *, archived=False) -> str:
    slug, owner_type = CASES[entity_type][2], CASES[entity_type][3]
    ev = generate_id()
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                linked_entity_type, linked_entity_id, archived_at)
           SELECT $1, $2, $3, t.id, $5, $4, CASE WHEN $7 THEN now() END
           FROM entity_event_types t WHERE t.slug = $6""",
        ev,
        owner_type,
        owner,
        target,
        entity_type,
        slug,
        archived,
    )
    return ev


@pytest.mark.parametrize("archived_event", [False, True], ids=["active", "archived"])
@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_inbound_linked_event_blocks_the_delete(client, db, entity_type, archived_event):
    target = await _entity(db, entity_type, archived=True)
    owner = await _entity(db, CASES[entity_type][3], archived=False)
    await _inbound_event(db, entity_type, owner, target, archived=archived_event)

    r = await client.delete(f"/admin/{CASES[entity_type][0]}/{target}/", headers=HTMX_HEADERS)

    assert r.status_code == 409, r.text
    assert "link" in r.json()["detail"].lower()
    table = CASES[entity_type][1]
    assert await db.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", target)


async def test_409_detail_names_the_linking_entity_and_event_type(client, db):
    target = await _entity(db, "organization", archived=True)
    owner = await _entity(db, "organization", archived=False, name="Predecessor Committee")
    await _inbound_event(db, "organization", owner, target)

    r = await client.delete(f"/admin/orgs/{target}/", headers=HTMX_HEADERS)

    detail = r.json()["detail"]
    assert "Predecessor Committee" in detail
    assert "Succeeded By" in detail
    assert "archive and delete" in detail.lower()


async def test_409_detail_counts_repeats_and_summarises_past_five(client, db):
    """One owner's repeated event type is counted; past five pairs, the rest summarise."""
    target = await _entity(db, "organization", archived=True)
    repeat = await _entity(db, "organization", archived=False, name="Org 0")
    for _ in range(2):
        await db.execute(
            """INSERT INTO entity_events
                   (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)
               SELECT $1, 'organization', $2, t.id, 'organization', $3
               FROM entity_event_types t WHERE t.slug = 'other'""",
            generate_id(),
            repeat,
            target,
        )
    for i in range(1, 7):
        owner = await _entity(db, "organization", archived=False, name=f"Org {i}")
        await _inbound_event(db, "organization", owner, target)

    r = await client.delete(f"/admin/orgs/{target}/", headers=HTMX_HEADERS)

    detail = r.json()["detail"]
    assert "Other on Org 0 (2 events)" in detail
    assert "Succeeded By on Org 4;" in detail
    assert "Org 5" not in detail and "Org 6" not in detail
    assert "and 2 more." in detail


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_delete_proceeds_once_the_inbound_event_is_gone(client, db, entity_type):
    target = await _entity(db, entity_type, archived=True)
    owner = await _entity(db, CASES[entity_type][3], archived=False)
    ev = await _inbound_event(db, entity_type, owner, target)
    await db.execute("DELETE FROM entity_events WHERE id = $1", ev)

    r = await client.delete(f"/admin/{CASES[entity_type][0]}/{target}/", headers=HTMX_HEADERS)

    assert r.status_code == 200, r.text


async def test_own_self_linking_event_does_not_block(client, db):
    """An entity's own events go with it (#605) — even one that links back to itself."""
    target = await _entity(db, "organization", archived=True)
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)
           SELECT $1, 'organization', $2, t.id, 'organization', $2
           FROM entity_event_types t WHERE t.slug = 'other'""",
        generate_id(),
        target,
    )

    r = await client.delete(f"/admin/orgs/{target}/", headers=HTMX_HEADERS)

    assert r.status_code == 200, r.text


@pytest_asyncio.fixture(loop_scope="session")
async def committed_orgs(db_pool):
    """Committed org ids, removed at teardown.

    Requested ahead of ``client``/``db`` so it tears down after them: the
    rollback client's open transaction may hold row locks on these rows until
    it rolls back, and cleanup would wait on it forever.
    """
    ids: list[str] = []
    yield ids
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE entity_id = ANY($1::text[])", ids)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::text[])", ids)


async def test_delete_waits_for_a_concurrent_link_then_refuses(committed_orgs, client, db, db_pool):
    """Across the seam: the check runs under the row lock, after the link commits.

    The producer's uncommitted link holds ``FOR KEY SHARE`` on the target (the
    schema trigger). Checking before locking would see nothing, then block at
    the ``DELETE`` and go ahead once the link commits — leaving it dangling.
    """
    target, owner = generate_id(), generate_id()
    committed_orgs += [target, owner]
    async with db_pool.acquire() as producer:
        await producer.execute(
            "INSERT INTO organizations (id, archived_at) VALUES ($1, now()), ($2, NULL)",
            target,
            owner,
        )
        async with producer.transaction():
            await _inbound_event(producer, "organization", owner, target)
            delete = asyncio.create_task(
                client.delete(f"/admin/orgs/{target}/", headers=HTMX_HEADERS)
            )
            await until_lock_waiting(producer, db.get_server_pid())
    r = await delete
    assert r.status_code == 409, r.text


# --- The admin link writers, when the target vanishes after their check ---
# Each writer validates the linked entity, then writes later; a delete landing in
# between trips the schema trigger. The curator gets the writer's own "not found"
# answer, not a 500. Each test deletes the target from a step that runs between
# the check and the write.


async def _event_type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM entity_event_types WHERE slug = $1", slug)


def _vanish_during_place_check(monkeypatch, target: str) -> None:
    """Delete ``target`` while the event form validates its place address."""

    async def _place_check(conn, raw_id):
        await conn.execute("DELETE FROM organizations WHERE id = $1", target)
        return None, None

    monkeypatch.setattr("src.api.admin._events_shared._validate_event_place_address", _place_check)


async def test_event_create_whose_link_vanishes_shows_the_form_error(client, db, monkeypatch):
    owner = await _entity(db, "organization", archived=False)
    target = await _entity(db, "organization", archived=True)
    _vanish_during_place_check(monkeypatch, target)

    r = await client.post(
        f"/admin/orgs/{owner}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": await _event_type_id(db, "other"),
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": target,
            "visibility": "public",
        },
    )

    assert r.status_code == 200, r.text
    assert "Linked entity not found" in r.text
    assert not await db.fetchval("SELECT 1 FROM entity_events WHERE entity_id = $1", owner)


async def test_event_edit_whose_new_link_vanishes_shows_the_form_error(client, db, monkeypatch):
    owner = await _entity(db, "organization", archived=False)
    target = await _entity(db, "organization", archived=True)
    etid = await _event_type_id(db, "other")
    ev = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, 'organization', $2, $3, 2020)",
        ev,
        owner,
        etid,
    )
    _vanish_during_place_check(monkeypatch, target)

    r = await client.post(
        f"/admin/orgs/{owner}/events/{ev}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": target,
            "visibility": "public",
        },
    )

    assert r.status_code == 200, r.text
    assert "Linked entity not found" in r.text
    assert await db.fetchval("SELECT linked_entity_id FROM entity_events WHERE id = $1", ev) is None


async def test_succession_link_whose_successor_vanishes_flashes_a_warning(client, db, monkeypatch):
    pred = await _entity(db, "organization", archived=False)
    succ = await _entity(db, "organization", archived=False)

    async def _chain_check(conn, id_a, id_b):
        await conn.execute("DELETE FROM organizations WHERE id = $1", succ)
        return False

    monkeypatch.setattr("src.api.admin.orgs_succession._in_same_chain", _chain_check)

    r = await client.post(f"/admin/orgs/{pred}/link-successor/{succ}/", headers=HTMX_HEADERS)

    # A warning flash, not a bare 404: the modal's hx-post has no error handler,
    # so a 4xx would leave the curator a dead button.
    assert r.status_code == 200, r.text
    assert "warning" in r.headers.get("HX-Trigger", "")
    assert "no longer exists" in r.headers.get("HX-Trigger", "")
    assert not await db.fetchval("SELECT 1 FROM entity_events WHERE entity_id = $1", pred)
