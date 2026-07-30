"""Integration tests for admin citation CRUD (#319).

Exercises the shared factory (src/api/admin/_citations_shared.py) through the
person router; the other four entity routers are identical instantiations.
"""

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
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


def _base(pid: str) -> str:
    return f"/admin/people/{pid}/citations"


async def test_create_renders_row(client, db, person):
    r = await client.post(
        f"{_base(person)}/",
        headers=HTMX,
        data={"field_name": "notes", "url": "https://s/a", "title": "Src A"},
    )
    assert r.status_code == 200, r.text
    assert "Src A" in r.text
    row = await db.fetchrow("SELECT * FROM citations WHERE entity_id=$1", person)
    assert row["url"] == "https://s/a"
    assert row["field_name"] == "notes"


async def test_create_unknown_field_rejected(client, db, person):
    r = await client.post(
        f"{_base(person)}/",
        headers=HTMX,
        data={"field_name": "bogus", "url": "https://s/a"},
    )
    assert r.status_code == 200
    assert "not a citable field" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_create_requires_url_or_title(client, db, person):
    r = await client.post(f"{_base(person)}/", headers=HTMX, data={"field_name": "notes"})
    assert r.status_code == 200
    assert "at least a URL or a title" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_duplicate_identity_flagged(client, db, person):
    await client.post(f"{_base(person)}/", headers=HTMX, data={"url": "https://s/dup"})
    r = await client.post(f"{_base(person)}/", headers=HTMX, data={"url": "https://s/dup"})
    assert "already exists" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 1


async def test_edit_updates_payload(client, db, person):
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/x','old')",
        cid,
        person,
    )
    r = await client.post(
        f"{_base(person)}/{cid}/edit-row/",
        headers=HTMX,
        data={"url": "https://s/x", "title": "new"},
    )
    assert r.status_code == 200
    assert (await db.fetchval("SELECT title FROM citations WHERE id=$1", cid)) == "new"


async def test_delete_removes_row(client, db, person):
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/x','t')",
        cid,
        person,
    )
    r = await client.request("DELETE", f"{_base(person)}/{cid}/", headers=HTMX)
    assert r.status_code == 200
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


async def test_requires_admin_auth(client, db, person):
    # No auth headers → get_admin_user issues a 307 login redirect (don't follow
    # it to the login stub) and nothing is written.
    r = await client.post(f"{_base(person)}/", data={"url": "https://s/x"}, follow_redirects=False)
    assert r.status_code == 307
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_detail_page_shows_citations_panel(client, db, person):
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/panel','Panel Src')",
        generate_id(),
        person,
    )
    r = await client.get(f"/admin/people/{person}/", headers=AUTH)
    assert r.status_code == 200
    assert "Citations" in r.text
    assert "Panel Src" in r.text
    # The permanent detail-page panel is not dismissible (no Close control) and
    # renders as a plain section, never a nested sub-row (that is drawer-only).
    assert "Close citations panel" not in r.text
    assert "citations-subrow" not in r.text
    # entity_id must reach the panel so its ids are entity-scoped (no empty suffix).
    assert f'id="citations-tbody-{person}"' in r.text


async def _seed_org(db) -> tuple[str, str]:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid, f"/admin/orgs/{oid}/"


async def _seed_role(db) -> tuple[str, str]:
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Director')", rid, oid
    )
    return rid, f"/admin/roles/{rid}/"


async def _seed_role_assignment(db) -> tuple[str, str]:
    oid, rid, pid, raid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        raid,
        pid,
        rid,
    )
    return raid, f"/admin/role-assignments/{raid}/"


async def _seed_jurisdiction(db) -> tuple[str, str]:
    jid = generate_id()
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,'Testland',$3)",
        jid,
        f"testland-{jid[-6:].lower()}",
        type_id,
    )
    return jid, f"/admin/jurisdictions/{jid}/"


@pytest.mark.parametrize(
    "entity_type,seeder",
    [
        ("organization", _seed_org),
        ("role", _seed_role),
        ("role_assignment", _seed_role_assignment),
        ("jurisdiction", _seed_jurisdiction),
    ],
)
async def test_all_detail_pages_render_citations_panel(client, db, entity_type, seeder):
    entity_id, detail = await seeder(db)
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,$2,$3,'https://s/panel',$4)",
        generate_id(),
        entity_type,
        entity_id,
        f"{entity_type} Src",
    )
    r = await client.get(detail, headers=AUTH)
    assert r.status_code == 200, r.text
    assert "Citations" in r.text
    assert f"{entity_type} Src" in r.text


# ── sub-entity inline citations (person_name, entity_event) ───────────────────


async def _seed_name(db) -> str:
    pid, nid = generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type) VALUES ($1,$2,'Jo','legal')",
        nid,
        pid,
    )
    return nid


async def _seed_event(db) -> str:
    oid, eid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id)"
        " VALUES ($1,'organization',$2,(SELECT id FROM entity_event_types LIMIT 1))",
        eid,
        oid,
    )
    return eid


async def test_person_name_inline_panel_and_create(client, db):
    nid = await _seed_name(db)
    # Inline panel GET renders the shared panel — styled with admin classes and,
    # because it's the inline drawer, a Close control (#319 styling fix).
    p = await client.get(f"/admin/person-names/{nid}/citations/", headers=AUTH)
    assert p.status_code == 200
    assert "Citations" in p.text
    assert 'class="entity-section"' in p.text  # not the non-existent "card"
    assert 'class="data-table"' in p.text  # not the non-existent "table"
    assert "Close citations panel" in p.text  # dismissible
    # Create a name-scoped citation.
    r = await client.post(
        f"/admin/person-names/{nid}/citations/",
        headers=HTMX,
        data={"field_name": "name", "url": "https://s/name", "title": "Name Src"},
    )
    assert r.status_code == 200, r.text
    row = await db.fetchrow(
        "SELECT * FROM citations WHERE entity_type='person_name' AND entity_id=$1", nid
    )
    assert row["url"] == "https://s/name"


async def test_entity_event_inline_panel_and_create(client, db):
    eid = await _seed_event(db)
    p = await client.get(f"/admin/entity-events/{eid}/citations/", headers=AUTH)
    assert p.status_code == 200
    r = await client.post(
        f"/admin/entity-events/{eid}/citations/",
        headers=HTMX,
        data={"field_name": "date", "url": "https://s/evt"},
    )
    assert r.status_code == 200, r.text
    assert (
        await db.fetchval(
            "SELECT count(*) FROM citations WHERE entity_type='entity_event' AND entity_id=$1", eid
        )
        == 1
    )


async def test_person_name_citation_touches_owning_person_via_admin(client, db):
    # Admin create on a name self-emits the owning person's 'updated' signal (trigger).
    nid = await _seed_name(db)
    pid = await db.fetchval("SELECT person_id FROM person_names WHERE id=$1", nid)
    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='person'"
        " AND entity_id=$1 AND change_kind='updated'",
        pid,
    )
    await client.post(
        f"/admin/person-names/{nid}/citations/",
        headers=HTMX,
        data={"field_name": "name", "url": "https://s/n"},
    )
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='person'"
        " AND entity_id=$1 AND change_kind='updated'",
        pid,
    )
    assert after > before


async def test_name_and_event_rows_show_cite_button(client, db):
    nid = await _seed_name(db)
    pid = await db.fetchval("SELECT person_id FROM person_names WHERE id=$1", nid)
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id)"
        " VALUES ($1,'person',$2,(SELECT id FROM entity_event_types WHERE applies_to IN"
        " ('person','both') LIMIT 1))",
        generate_id(),
        pid,
    )
    r = await client.get(f"/admin/people/{pid}/", headers=AUTH)
    assert r.status_code == 200
    assert f"/admin/person-names/{nid}/citations/" in r.text
    # The Cite button tethers the panel to its own row (afterend), not a shared
    # bottom drawer — the drawer divs are gone (#319 scoping fix).
    assert f'hx-target="#name-row-{nid}"' in r.text
    assert 'hx-swap="afterend"' in r.text
    assert 'id="names-citations-drawer"' not in r.text
    assert 'id="events-citations-drawer"' not in r.text


async def test_person_name_panel_is_scoped_labeled_subrow(client, db):
    nid = await _seed_name(db)  # name "Jo", type legal
    p = await client.get(f"/admin/person-names/{nid}/citations/", headers=AUTH)
    assert p.status_code == 200
    # Rendered as a full-width sub-row so it nests under the clicked name row.
    assert "citations-subrow" in p.text
    # colspan must equal the names table's exact column count (Name/Type/Canonical/
    # actions = 4). An over-large colspan (e.g. 99) implies phantom columns that
    # collapse the real ones under table-layout:fixed (#319 scrunch regression).
    assert 'colspan="4"' in p.text
    assert 'colspan="99"' not in p.text
    # Heading names the subject so it can't be confused with the person panel.
    assert "Jo" in p.text
    assert "for" in p.text  # "Citations for …"


async def test_entity_event_panel_subrow_spans_events_table(client, db):
    eid = await _seed_event(db)
    p = await client.get(f"/admin/entity-events/{eid}/citations/", headers=AUTH)
    assert p.status_code == 200
    assert "citations-subrow" in p.text
    # Events table has 6 columns (Type/Date/Place/Linked/Status/actions).
    assert 'colspan="6"' in p.text
    assert 'colspan="99"' not in p.text


async def test_person_name_new_row_locks_field(client, db):
    nid = await _seed_name(db)
    r = await client.get(f"/admin/person-names/{nid}/citations/new-row/", headers=AUTH)
    assert r.status_code == 200
    # No field picker on a name citation — it is always about the name.
    assert 'name="field_name"' in r.text
    assert "<select" not in r.text
    assert 'type="hidden" name="field_name" value="name"' in r.text


async def test_person_name_create_forces_locked_field(client, db):
    nid = await _seed_name(db)
    # Even if a rogue client posts a different field_name, the server pins it.
    r = await client.post(
        f"/admin/person-names/{nid}/citations/",
        headers=HTMX,
        data={"field_name": "notes", "url": "https://s/x"},
    )
    assert r.status_code == 200, r.text
    row = await db.fetchrow(
        "SELECT * FROM citations WHERE entity_type='person_name' AND entity_id=$1", nid
    )
    assert row["field_name"] == "name"


async def test_entity_event_new_row_keeps_field_selector(client, db):
    eid = await _seed_event(db)
    r = await client.get(f"/admin/entity-events/{eid}/citations/new-row/", headers=AUTH)
    assert r.status_code == 200
    # Events have multiple citable fields → the selector is legitimate.
    assert '<select name="field_name"' in r.text


# ── sub-entity delete drops its citations (no orphan) ─────────────────────────


async def test_admin_event_delete_drops_citations(client, db):
    oid, eid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, archived_at)"
        " VALUES ($1,'organization',$2,(SELECT id FROM entity_event_types WHERE applies_to IN"
        " ('organization','both') LIMIT 1), now())",  # archived → eligible for hard delete
        eid,
        oid,
    )
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'entity_event',$2,'https://s/evt','t')",
        cid,
        eid,
    )
    r = await client.request("DELETE", f"/admin/orgs/{oid}/events/{eid}/", headers=HTMX)
    assert r.status_code == 200, r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


async def test_admin_name_delete_drops_citations(client, db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    n1, n2 = generate_id(), generate_id()
    # Two names so deleting one isn't blocked by the last-identity guard.
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1,$2,'Keep','legal',TRUE), ($3,$2,'Drop','variant',FALSE)",
        n1,
        pid,
        n2,
    )
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, field_name, url, title)"
        " VALUES ($1,'person_name',$2,'name','https://s/n','t')",
        cid,
        n2,
    )
    r = await client.request("DELETE", f"/admin/people/{pid}/names/{n2}/", headers=HTMX)
    assert r.status_code == 200, r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0
