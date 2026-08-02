"""Integration tests for the admin role-assignment relationships panel (#301)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX = {**AUTH, "HX-Request": "true"}


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


async def _assignment(db, person_name, role_title, start=None, end=None):
    org, person, role, aid = generate_id(), generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        person,
        person_name,
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", role, org, role_title
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, is_current)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        aid,
        person,
        role,
        start,
        end,
        end is None,  # is_current cannot be TRUE with an end_date (chk_current_no_end_date)
    )
    return aid


async def _staff_of_id(db):
    return await db.fetchval(
        "SELECT id FROM role_assignment_relationship_types WHERE slug='staff_of'"
    )


def _base(ra_id):
    return f"/admin/role-assignments/{ra_id}/relationships"


async def test_create_htmx_returns_row(client, db):
    staffer = await _assignment(db, "Kate Armstrong", "Legislative Aide")
    principal = await _assignment(db, "June Robinson", "Senator")
    rt = await _staff_of_id(db)
    r = await client.post(
        f"{_base(staffer)}/",
        headers=HTMX,
        data={"target_id": principal, "rel_type_id": rt, "direction": "outgoing"},
    )
    assert r.status_code == 200, r.text
    assert "June Robinson" in r.text
    assert "HX-Trigger" in r.headers  # flash


async def test_create_non_htmx_redirects_with_flash(client, db):
    staffer = await _assignment(db, "Joren Clowers", "Legislative Assistant")
    principal = await _assignment(db, "Shelley Kloba", "Representative")
    rt = await _staff_of_id(db)
    r = await client.post(
        f"{_base(staffer)}/",
        headers=AUTH,  # no HX-Request
        data={"target_id": principal, "rel_type_id": rt, "direction": "outgoing"},
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/role-assignments/{staffer}/?flash=saved"


async def test_create_self_rejected(client, db):
    a = await _assignment(db, "Solo", "Aide")
    rt = await _staff_of_id(db)
    r = await client.post(f"{_base(a)}/", headers=HTMX, data={"target_id": a, "rel_type_id": rt})
    assert r.status_code == 422
    assert "itself" in r.text


async def test_create_out_of_window_rejected(client, db):
    import datetime

    staffer = await _assignment(
        db, "Coco Chang", "Aide", start=datetime.date(2023, 1, 1), end=datetime.date(2024, 12, 31)
    )
    principal = await _assignment(
        db, "Saldana", "Senator", start=datetime.date(2023, 1, 1), end=datetime.date(2024, 12, 31)
    )
    rt = await _staff_of_id(db)
    r = await client.post(
        f"{_base(staffer)}/",
        headers=HTMX,
        data={
            "target_id": principal,
            "rel_type_id": rt,
            "valid_from": "2020-01-01",  # precedes both windows
        },
    )
    assert r.status_code == 422
    assert "outside" in r.text.lower()


async def test_edit_and_delete(client, db):
    staffer = await _assignment(db, "Aide P", "Aide")
    principal = await _assignment(db, "Boss P", "Senator")
    rt = await _staff_of_id(db)
    created = await client.post(
        f"{_base(staffer)}/", headers=HTMX, data={"target_id": principal, "rel_type_id": rt}
    )
    rel_id = await db.fetchval(
        "SELECT id FROM role_assignment_relationships WHERE from_assignment_id=$1", staffer
    )
    assert rel_id and created.status_code == 200

    # edit window
    e = await client.post(
        f"{_base(staffer)}/{rel_id}/edit-row/",
        headers=HTMX,
        data={"valid_from": "2023-01-01", "valid_until": "2024-12-31", "notes": "chief aide"},
    )
    assert e.status_code == 200
    row = await db.fetchrow(
        "SELECT valid_from, notes FROM role_assignment_relationships WHERE id=$1", rel_id
    )
    assert str(row["valid_from"]) == "2023-01-01"
    assert row["notes"] == "chief aide"

    # delete → soft-delete (archived_at set)
    d = await client.request("DELETE", f"{_base(staffer)}/{rel_id}/", headers=HTMX)
    assert d.status_code == 200
    archived = await db.fetchval(
        "SELECT archived_at FROM role_assignment_relationships WHERE id=$1", rel_id
    )
    assert archived is not None


async def test_search_returns_matches(client, db):
    staffer = await _assignment(db, "Searcher", "Aide")
    await _assignment(db, "Findme Person", "Senator")
    r = await client.get(f"{_base(staffer)}/search/?q=Findme", headers=AUTH)
    assert r.status_code == 200
    assert "Findme Person" in r.text


async def test_detail_page_renders_panel(client, db):
    staffer = await _assignment(db, "Panel P", "Aide")
    principal = await _assignment(db, "Panel Boss", "Senator")
    rt = await _staff_of_id(db)
    await client.post(
        f"{_base(staffer)}/", headers=HTMX, data={"target_id": principal, "rel_type_id": rt}
    )
    d = await client.get(f"/admin/role-assignments/{staffer}/", headers=AUTH)
    assert d.status_code == 200
    assert "Relationships" in d.text
    assert "Panel Boss" in d.text


async def test_create_archived_target_rejected(client, db):
    """A direct POST naming an archived target (typeahead hides them) is refused —
    no edge to a logically-dead endpoint (#301 CR)."""
    staffer = await _assignment(db, "Live Staffer", "Aide")
    principal = await _assignment(db, "Retired Boss", "Senator")
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id=$1", principal)
    rt = await _staff_of_id(db)
    r = await client.post(
        f"{_base(staffer)}/",
        headers=HTMX,
        data={"target_id": principal, "rel_type_id": rt, "direction": "outgoing"},
    )
    assert r.status_code == 422, r.text
    assert "archived" in r.text
    minted = await db.fetchval(
        "SELECT count(*) FROM role_assignment_relationships WHERE from_assignment_id=$1", staffer
    )
    assert minted == 0
