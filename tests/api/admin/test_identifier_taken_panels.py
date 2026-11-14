"""#812: the identifier panels refuse a ``*_cannabis_observer`` value another row holds.

``uq_identifiers_cannabis_observer`` makes one value of each of the four types
name one entity. Every panel's create and edit run their write in a savepoint
(#424): a collision answers a ``warning`` flash naming the holder and re-swaps
the rows as they stand, rather than a 500 — and the request's connection stays
usable. An edit can also *retype* a row into one of the four types, which
collides the same way.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.ancillary_migrate import ENTITY_TABLES
from src.core.db import generate_id
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}

PANELS = {
    "organization": "orgs",
    "person": "people",
    "role": "roles",
    "role_assignment": "role-assignments",
}
UNIQUE_SLUGS = {
    "organization": "org_cannabis_observer",
    "person": "person_cannabis_observer",
    "role": "role_cannabis_observer",
    "role_assignment": "role_assignment_cannabis_observer",
}
#: A repeatable type to retype *from*. A role has none: its only external type is unique.
REPEATABLE_SLUGS = {
    "organization": "org_ubi",
    "person": "person_ssn",
    "role_assignment": "role_wa_pdc",
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _entity(db, entity_type: str, *, archived: bool = False) -> str:
    eid = await archived_entity(db, entity_type)
    if not archived:
        await db.execute(
            f"UPDATE {ENTITY_TABLES[entity_type]} SET archived_at = NULL WHERE id = $1", eid
        )
    return eid


async def _type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug = $1", slug)


async def _identify(db, entity_id: str, slug: str, value: str) -> str:
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        entity_id,
        await _type_id(db, slug),
        value,
    )
    return iid


async def _rows(db, entity_id: str) -> list[tuple[str, str]]:
    return [
        (r["slug"], r["value"])
        for r in await db.fetch(
            "SELECT t.slug, i.value FROM identifiers i"
            " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
            " WHERE i.entity_id = $1 ORDER BY t.slug, i.value",
            entity_id,
        )
    ]


def _url(entity_type: str, entity_id: str, row: str | None = None) -> str:
    base = f"/admin/{PANELS[entity_type]}/{entity_id}/identifiers/"
    return f"{base}{row}/edit-row/" if row else base


def _assert_taken(r, holder: str) -> None:
    assert r.status_code == 200
    trigger = r.headers["HX-Trigger"]
    assert "warning" in trigger
    assert holder in trigger
    assert "<form" not in r.text  # the rows as they stand, not the form


# --- create -------------------------------------------------------------------------


@pytest.mark.parametrize("entity_type", sorted(PANELS))
async def test_create_of_a_value_another_entity_holds_is_refused(client, db, entity_type):
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, UNIQUE_SLUGS[entity_type], "01SITEULID")

    r = await client.post(
        _url(entity_type, target),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, UNIQUE_SLUGS[entity_type]),
            "value": " 01SITEULID ",
        },
    )

    _assert_taken(r, holder)
    assert await _rows(db, target) == []  # and the connection is still usable


async def test_create_names_an_archived_holder_as_archived(client, db):
    """#481: the curator's fix is to unarchive (or merge) the holder."""
    holder = await _entity(db, "organization", archived=True)
    target = await _entity(db, "organization")
    await _identify(db, holder, "org_cannabis_observer", "01SITEULID")

    r = await client.post(
        _url("organization", target),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "org_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    _assert_taken(r, holder)
    assert "archived" in r.headers["HX-Trigger"]


async def test_create_of_a_value_this_entity_already_holds_is_refused(client, db):
    target = await _entity(db, "person")
    await _identify(db, target, "person_cannabis_observer", "01SITEULID")

    r = await client.post(
        _url("person", target),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "person_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"]
    assert "this record" in r.headers["HX-Trigger"]
    assert await _rows(db, target) == [("person_cannabis_observer", "01SITEULID")]


async def test_create_collision_falls_back_to_a_flash_redirect(client, db):
    holder = await _entity(db, "person")
    target = await _entity(db, "person")
    await _identify(db, holder, "person_cannabis_observer", "01SITEULID")

    r = await client.post(
        _url("person", target),
        headers=AUTH_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "person_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    assert r.status_code == 303
    assert r.headers["location"].endswith("flash=exists")


# --- edit ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity_type", sorted(PANELS))
async def test_edit_to_a_value_another_entity_holds_is_refused(client, db, entity_type):
    slug = UNIQUE_SLUGS[entity_type]
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, slug, "01SITEULID")
    row = await _identify(db, target, slug, "01MINE")

    r = await client.post(
        _url(entity_type, target, row),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, slug), "value": "01SITEULID"},
    )

    _assert_taken(r, holder)
    assert await _rows(db, target) == [(slug, "01MINE")]


@pytest.mark.parametrize("entity_type", sorted(REPEATABLE_SLUGS))
async def test_a_retype_into_a_held_value_is_refused(client, db, entity_type):
    """The row's value was free under its old type; under the new one it is taken."""
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, UNIQUE_SLUGS[entity_type], "01SITEULID")
    row = await _identify(db, target, REPEATABLE_SLUGS[entity_type], "01SITEULID")

    r = await client.post(
        _url(entity_type, target, row),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, UNIQUE_SLUGS[entity_type]),
            "value": "01SITEULID",
        },
    )

    _assert_taken(r, holder)
    assert await _rows(db, target) == [(REPEATABLE_SLUGS[entity_type], "01SITEULID")]


async def test_a_retype_into_a_free_value_saves(client, db):
    target = await _entity(db, "organization")
    row = await _identify(db, target, "org_ubi", "01SITEULID")

    r = await client.post(
        _url("organization", target, row),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "org_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    assert r.status_code == 200
    assert "success" in r.headers["HX-Trigger"]
    assert await _rows(db, target) == [("org_cannabis_observer", "01SITEULID")]


async def test_saving_a_row_unchanged_is_not_a_collision_with_itself(client, db):
    target = await _entity(db, "person")
    row = await _identify(db, target, "person_cannabis_observer", "01SITEULID")

    r = await client.post(
        _url("person", target, row),
        headers=HTMX_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "person_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    assert "success" in r.headers["HX-Trigger"]


async def test_edit_collision_falls_back_to_a_flash_redirect(client, db):
    holder = await _entity(db, "person")
    target = await _entity(db, "person")
    await _identify(db, holder, "person_cannabis_observer", "01SITEULID")
    row = await _identify(db, target, "person_cannabis_observer", "01MINE")

    r = await client.post(
        _url("person", target, row),
        headers=AUTH_HEADERS,
        data={
            "entity_identifier_type_id": await _type_id(db, "person_cannabis_observer"),
            "value": "01SITEULID",
        },
    )

    assert r.status_code == 303
    assert r.headers["location"].endswith("flash=exists")
