"""Integration tests for person events CRUD."""

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
async def person_and_event_type(db):
    """One person + one event type seeded for all tests."""
    pid = generate_id()
    etid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO entity_event_types (id, slug, display_name, applies_to)"
        " VALUES ($1, $2, $3, 'person')",
        etid,
        f"test-event-{etid[:8]}",
        "Test Event",
    )

    yield pid, etid


@pytest_asyncio.fixture(loop_scope="session")
async def person_with_event(db, person_and_event_type):
    """Person with a single non-archived event."""
    pid, etid = person_and_event_type
    eid = generate_id()

    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year, visibility)"
        " VALUES ($1, 'person', $2, $3, 2020, 'public')",
        eid,
        pid,
        etid,
    )

    yield pid, etid, eid


# ---------------------------------------------------------------------------
# GET new-row
# ---------------------------------------------------------------------------


async def test_event_new_row_returns_form(client, person_and_event_type):
    pid, _ = person_and_event_type
    r = await client.get(f"/admin/people/{pid}/events/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_event_new_row_no_auth_returns_307(client, person_and_event_type):
    pid, _ = person_and_event_type
    r = await client.get(f"/admin/people/{pid}/events/new-row/", follow_redirects=False)
    assert r.status_code == 307


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------


async def test_event_create_returns_read_row(client, person_and_event_type, db):
    pid, etid = person_and_event_type
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2021",
            "event_month": "6",
            "event_day": "15",
            "notes": "Test note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Event" in r.text
    assert "HX-Trigger" in r.headers


async def test_event_create_requires_year_for_typed_event(client, db):
    """Event type with requires_year=True must reject missing year."""
    pid = generate_id()
    etid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO entity_event_types"
        " (id, slug, display_name, applies_to, requires_year)"
        " VALUES ($1, $2, 'Dated Event', 'person', TRUE)",
        etid,
        f"dated-event-{etid[:8]}",
    )

    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={"event_type_id": etid, "event_year": "", "visibility": "public"},
    )
    assert r.status_code == 200
    assert "Year is required" in r.text


async def test_event_create_requires_linked_entity_for_typed_event(client, db):
    """Event type with requires_linked_entity=True must reject missing linked_entity_id."""
    pid = generate_id()
    etid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO entity_event_types"
        " (id, slug, display_name, applies_to, requires_linked_entity)"
        " VALUES ($1, $2, 'Linked Event', 'person', TRUE)",
        etid,
        f"linked-event-{etid[:8]}",
    )

    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={"event_type_id": etid, "linked_entity_id": "", "visibility": "public"},
    )
    assert r.status_code == 200
    assert "Linked entity is required" in r.text


# ---------------------------------------------------------------------------
# GET read-row / edit-row
# ---------------------------------------------------------------------------


async def test_event_read_row_returns_row(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.get(f"/admin/people/{pid}/events/{eid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" not in r.text
    assert "Test Event" in r.text


async def test_event_edit_row_returns_form(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.get(f"/admin/people/{pid}/events/{eid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


# ---------------------------------------------------------------------------
# POST edit-row (update)
# ---------------------------------------------------------------------------


async def test_event_update_saves_and_returns_read_row(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2022",
            "notes": "Updated note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Event" in r.text
    assert "<form" not in r.text


async def test_event_edit_requires_year_for_typed_event(client, db, person_and_event_type):
    """POST edit-row with requires_year event type and no year returns form with error."""
    pid, _ = person_and_event_type
    etid = generate_id()
    eid = generate_id()

    await db.execute(
        "INSERT INTO entity_event_types"
        " (id, slug, display_name, applies_to, requires_year)"
        " VALUES ($1, $2, 'Dated Edit Event', 'person', TRUE)",
        etid,
        f"dated-edit-event-{etid[:8]}",
    )
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year, visibility)"
        " VALUES ($1, 'person', $2, $3, 2020, 'public')",
        eid,
        pid,
        etid,
    )

    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"event_type_id": etid, "event_year": "", "visibility": "public"},
    )
    assert r.status_code == 200
    assert "Year is required" in r.text


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------


async def test_event_archive_sets_archived_at(client, person_with_event, db):
    pid, etid, eid = person_with_event
    r = await client.post(f"/admin/people/{pid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers

    row = await db.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is not None

    # Restore for subsequent tests
    await db.execute("UPDATE entity_events SET archived_at=NULL WHERE id=$1", eid)


async def test_event_archive_already_archived_returns_409(client, db, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
        " VALUES ($1, 'person', $2, $3, 'public', NOW())",
        eid,
        pid,
        etid,
    )

    r = await client.post(f"/admin/people/{pid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 409


async def test_event_unarchive_clears_archived_at(client, db, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
        " VALUES ($1, 'person', $2, $3, 'public', NOW())",
        eid,
        pid,
        etid,
    )

    r = await client.post(f"/admin/people/{pid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    row = await db.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is None


async def test_event_unarchive_not_archived_returns_409(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.post(f"/admin/people/{pid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


async def test_event_delete_not_archived_returns_409(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.delete(f"/admin/people/{pid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 409


async def test_event_delete_archived_succeeds(client, db, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
        " VALUES ($1, 'person', $2, $3, 'public', NOW())",
        eid,
        pid,
        etid,
    )

    r = await client.delete(f"/admin/people/{pid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert r.text == ""

    row = await db.fetchrow("SELECT id FROM entity_events WHERE id=$1", eid)
    assert row is None  # hard-deleted


# ---------------------------------------------------------------------------
# event_place_address_id linkage
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def address_city(db):
    """A street-precision address row."""
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses"
        " (id, raw_input, city, region, country, standardized, precision)"
        " VALUES ($1, 'Portland OR', 'Portland', 'OR', 'US',"
        " '1 Main St, Portland, OR', 'street')",
        aid,
    )
    yield aid


@pytest_asyncio.fixture(loop_scope="session")
async def address_low_precision(db):
    """A region-precision address row — below the city threshold."""
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses"
        " (id, raw_input, region, country, precision)"
        " VALUES ($1, 'Oregon', 'OR', 'US', 'region')",
        aid,
    )
    yield aid


async def test_event_create_links_address(client, person_and_event_type, address_city, db):
    pid, etid = person_and_event_type
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "1990",
            "event_place_text": "Portland",
            "event_place_address_id": address_city,
            "notes": "PeopleAddrLinkTest",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text

    row = await db.fetchrow(
        "SELECT event_place_address_id FROM entity_events"
        " WHERE entity_id=$1 AND notes='PeopleAddrLinkTest'",
        pid,
    )
    assert row is not None
    assert row["event_place_address_id"] == address_city


async def test_event_create_address_low_precision_returns_form_error(
    client, person_and_event_type, address_low_precision
):
    pid, etid = person_and_event_type
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_place_address_id": address_low_precision,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "precision" in r.text.lower()


async def test_event_create_address_not_found_returns_form_error(client, person_and_event_type):
    pid, etid = person_and_event_type
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_place_address_id": "nonexistent-address-id",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "not found" in r.text.lower()


async def test_event_edit_links_address(client, person_with_event, address_city, db):
    pid, etid, eid = person_with_event
    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "event_place_address_id": address_city,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text

    row = await db.fetchrow("SELECT event_place_address_id FROM entity_events WHERE id=$1", eid)
    assert row["event_place_address_id"] == address_city


async def test_event_edit_clears_address(client, person_and_event_type, address_city, db):
    pid, etid = person_and_event_type
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year,"
        "  event_place_address_id, visibility)"
        " VALUES ($1, 'person', $2, $3, 1990, $4, 'public')",
        eid,
        pid,
        etid,
        address_city,
    )

    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "1990",
            "event_place_address_id": "",
            "visibility": "public",
        },
    )
    assert r.status_code == 200

    row = await db.fetchrow("SELECT event_place_address_id FROM entity_events WHERE id=$1", eid)
    assert row["event_place_address_id"] is None


async def test_event_read_row_shows_address_city(client, person_and_event_type, address_city, db):
    pid, etid = person_and_event_type
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year,"
        "  event_place_address_id, visibility)"
        " VALUES ($1, 'person', $2, $3, 1990, $4, 'public')",
        eid,
        pid,
        etid,
        address_city,
    )

    r = await client.get(f"/admin/people/{pid}/events/{eid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Portland" in r.text


# ---------------------------------------------------------------------------
# Linked-entity validation + edit prefill (#172)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def linkable_org(db):
    """An organization with a display name, usable as a linked entity."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Linked Target Org', TRUE)",
        generate_id(),
        oid,
    )

    yield oid, "Linked Target Org"


async def test_event_create_accepts_valid_linked_entity(
    client, person_and_event_type, linkable_org, db
):
    pid, etid = person_and_event_type
    oid, _ = linkable_org
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": oid,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity" not in r.text  # no validation error
    row = await db.fetchrow(
        "SELECT linked_entity_type, linked_entity_id FROM entity_events"
        " WHERE entity_id=$1 AND linked_entity_id=$2",
        pid,
        oid,
    )
    assert row is not None
    assert row["linked_entity_type"] == "organization"


async def test_event_create_rejects_unknown_linked_entity(client, person_and_event_type):
    pid, etid = person_and_event_type
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": "org_doesnotexist",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity not found" in r.text


async def test_event_create_rejects_type_mismatch(client, person_and_event_type, db):
    """An id that exists as a person must not validate when type says organization."""
    pid, etid = person_and_event_type
    other_pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other_pid)
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": other_pid,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity not found" in r.text


async def test_event_create_rejects_linked_id_without_type(
    client, person_and_event_type, linkable_org
):
    pid, etid = person_and_event_type
    oid, _ = linkable_org
    r = await client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "",
            "linked_entity_id": oid,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "person or organization" in r.text


async def test_event_edit_row_prefills_linked_entity_name(
    client, person_and_event_type, linkable_org, db
):
    pid, etid = person_and_event_type
    oid, oname = linkable_org
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year,"
        "  linked_entity_type, linked_entity_id, visibility)"
        " VALUES ($1, 'person', $2, $3, 2020, 'organization', $4, 'public')",
        eid,
        pid,
        etid,
        oid,
    )
    r = await client.get(f"/admin/people/{pid}/events/{eid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert oname in r.text  # display name, not the raw ULID


async def test_event_edit_rejects_unknown_linked_entity(client, person_with_event):
    pid, etid, eid = person_with_event
    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "organization",
            "linked_entity_id": "org_doesnotexist",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity not found" in r.text


async def test_event_edit_unchanged_dangling_link_still_saves(client, person_and_event_type, db):
    """Editing an unrelated field must not re-validate an unchanged (dangling) link.

    A linked target can be hard-deleted or merged away after the link is made,
    leaving entity_events.linked_entity_id dangling (polymorphic ref, no FK). The
    admin must still be able to edit other fields without first repointing the link.
    """
    pid, etid = person_and_event_type
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events"
        " (id, entity_type, entity_id, event_type_id, event_year,"
        "  linked_entity_type, linked_entity_id, visibility)"
        " VALUES ($1, 'person', $2, $3, 2020, 'organization', 'org_ghost_deleted', 'public')",
        eid,
        pid,
        etid,
    )
    r = await client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2021",
            "linked_entity_type": "organization",
            "linked_entity_id": "org_ghost_deleted",  # unchanged dangling link
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity not found" not in r.text
    assert "<form" not in r.text  # read-row returned ⇒ saved
    yr = await db.fetchval("SELECT event_year FROM entity_events WHERE id=$1", eid)
    assert yr == 2021


async def test_event_form_linked_typeahead_hx_include_is_row_scoped(client, person_and_event_type):
    """hx-include must target this row's own type select by id, not a global

    name selector — otherwise concurrently-open rows cross-contaminate scope.
    """
    pid, _ = person_and_event_type
    r = await client.get(f"/admin/people/{pid}/events/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'id="linked-entity-type-new"' in r.text
    assert 'hx-include="#linked-entity-type-new"' in r.text
    assert "[name='linked_entity_type']" not in r.text


# ---------------------------------------------------------------------------
# Cite button citation count (#341)
# ---------------------------------------------------------------------------


async def test_event_row_cite_button_shows_count(client, db, person_with_event):
    pid, _etid, eid = person_with_event
    for url in ("https://example.com/a", "https://example.com/b"):
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id, url)"
            " VALUES ($1, 'entity_event', $2, $3)",
            generate_id(),
            eid,
            url,
        )
    r = await client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Cite (2)" in r.text


async def test_event_row_cite_button_plain_without_citations(client, person_with_event):
    pid, _etid, _eid = person_with_event
    r = await client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Cite (" not in r.text


async def test_event_row_cite_count_excludes_archived(client, db, person_with_event):
    pid, _etid, eid = person_with_event
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'entity_event', $2, 'https://example.com/a')",
        generate_id(),
        eid,
    )
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, archived_at)"
        " VALUES ($1, 'entity_event', $2, 'https://example.com/x', now())",
        generate_id(),
        eid,
    )
    r = await client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Cite (1)" in r.text
