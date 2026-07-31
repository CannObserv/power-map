"""Integration tests for person names CRUD."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.people_names import _maybe_promote_sole_name
from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.html_slices import table_html

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
async def person_and_name(db):
    pid, nid = generate_id(), generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Original Name', 'legal', TRUE)",
        nid,
        pid,
    )

    return pid, nid


@pytest_asyncio.fixture(loop_scope="session")
async def person_only(db):
    """One person row, no names yet (for create-flow tests)."""
    pid = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)

    return {"pid": pid}


async def _fetch_canonical_name(db, person_id: str) -> dict | None:
    row = await db.fetchrow(
        "SELECT name, name_type, is_canonical, visibility,"
        " locale, script, sort_as"
        " FROM person_names WHERE person_id=$1 AND is_canonical=TRUE",
        person_id,
    )
    return dict(row) if row else None


async def _insert_second_name(
    db, person_id: str, name_id: str, name: str = "Former Name", name_type: str = "former"
) -> None:
    """Insert a second (non-canonical) name for an existing person."""
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, $4, FALSE)",
        name_id,
        person_id,
        name,
        name_type,
    )


async def _fetch_is_canonical(db, name_id: str) -> bool | None:
    row = await db.fetchrow("SELECT is_canonical FROM person_names WHERE id=$1", name_id)
    return row["is_canonical"] if row else None


async def test_names_create(client, person_and_name):
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "Former Name" in r.text


async def test_names_read_row(client, person_and_name):
    pid, nid = person_and_name
    r = await client.get(f"/admin/people/{pid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


async def test_names_edit_row_get(client, person_and_name):
    pid, nid = person_and_name
    r = await client.get(f"/admin/people/{pid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


async def test_names_update(client, person_and_name):
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


async def test_names_update_returns_success_flash(client, person_and_name):
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Updated Name" in trigger["showFlash"]["body"]


async def test_names_delete(client, person_and_name, db):
    pid, _ = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db, pid, nid2)

    r = await client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_names_delete_last_blocked(client, person_and_name, db):
    """Deleting the last name must be blocked with an error flash."""
    pid, nid = person_and_name
    r = await client.delete(f"/admin/people/{pid}/names/{nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"

    row = await db.fetchrow("SELECT id FROM person_names WHERE id=$1", nid)
    assert row is not None


async def test_names_delete_last_non_htmx_returns_409(client, person_and_name):
    pid, nid = person_and_name
    r = await client.delete(f"/admin/people/{pid}/names/{nid}/", headers=AUTH_HEADERS)
    assert r.status_code == 409


async def test_name_edit_sole_uncanonical_is_blocked(client, person_and_name, db):
    """Unchecking canonical on the only name must be blocked with an error flash."""
    pid, nid = person_and_name

    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"

    is_canonical = await _fetch_is_canonical(db, nid)
    assert is_canonical is True, "sole name must remain canonical after blocked edit"


async def test_name_edit_uncanonical_with_multiple_names_blocked(client, person_and_name, db):
    """Unchecking canonical on the only canonical name (when others exist) must be blocked."""
    pid, canonical_nid = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db, pid, other_nid)

    r = await client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"

    is_canonical = await _fetch_is_canonical(db, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_uncanonical_non_htmx_redirects(client, person_and_name, db):
    """Non-HTMX path: unchecking canonical on only canonical (multiple names) must redirect."""
    pid, canonical_nid = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db, pid, other_nid)

    r = await client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=AUTH_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    is_canonical = await _fetch_is_canonical(db, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_non_canonical_row_can_stay_non_canonical(client, person_and_name, db):
    """Editing a non-canonical name without checking canonical must succeed."""
    pid, _ = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db, pid, other_nid)

    r = await client.post(
        f"/admin/people/{pid}/names/{other_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Renamed Former", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# Parity tests (mirror test_orgs_names.py coverage)
# ---------------------------------------------------------------------------


async def test_names_new_row_returns_form(client, person_and_name):
    pid, _ = person_and_name
    r = await client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_names_form_row_canonical_toggle_has_aria_label(client, person_and_name):
    pid, _ = person_and_name
    r = await client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Canonical"' in r.text


async def test_names_edit_returns_tbody(client, person_and_name, db):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    pid, nid = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db, pid, nid2, name="Second Name")

    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert f'id="name-row-{nid}"' in r.text
    assert f'id="name-row-{nid2}"' in r.text
    assert "<table" not in r.text


async def test_names_create_returns_update_person_header(client, person_and_name):
    """Creating a name must emit updatePersonHeader in HX-Trigger."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


async def test_names_update_returns_update_person_header_with_new_display(client, person_and_name):
    """Updating the canonical name must emit updatePersonHeader."""
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Renamed Person", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


async def test_names_delete_returns_update_person_header(client, person_and_name, db):
    """Deleting a non-canonical name must emit updatePersonHeader."""
    pid, _ = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db, pid, nid2)

    r = await client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


# ---------------------------------------------------------------------------
# Combined name + parts payload (Issue #127 Task D)
# ---------------------------------------------------------------------------


async def test_names_create_accepts_combined_parts_payload(client, person_and_name, db):
    """Issue #127: POST / accepts parts fields and seeds person_name_parts."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Ada Lovelace",
            "name_type": "preferred",
            "is_canonical": "",
            "given_names": ["Ada"],
            "family_names": ["Lovelace"],
            "primary_identifier": "family",
        },
    )
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        "SELECT pn.id AS nid, pnp.given_names, pnp.family_names,"
        " pnp.primary_identifier"
        " FROM person_names pn"
        " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
        " WHERE pn.person_id=$1 AND pn.name='Ada Lovelace'",
        pid,
    )

    assert row is not None
    assert row["given_names"] == ["Ada"]
    assert row["family_names"] == ["Lovelace"]
    assert row["primary_identifier"] == "family"


async def test_names_update_accepts_combined_parts_payload(client, person_and_name, db):
    """Issue #127: POST /edit-row/ updates name AND upserts parts in one transaction."""
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Renamed",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Re"],
            "family_names": ["Named"],
        },
    )
    assert r.status_code == 200, r.text

    row = await db.fetchrow(
        "SELECT pn.name, pnp.given_names, pnp.family_names"
        " FROM person_names pn"
        " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
        " WHERE pn.id=$1",
        nid,
    )

    assert row["name"] == "Renamed"
    assert row["given_names"] == ["Re"]
    assert row["family_names"] == ["Named"]


# ---------------------------------------------------------------------------
# New-name form metadata E2E (Issue #130)
# ---------------------------------------------------------------------------


async def test_create_new_name_persists_metadata_fields(client, person_only, db):
    """Issue #130: POSTing the new-name form with metadata fields must write
    visibility/locale/script/sort_as to person_names. Regression coverage for
    the inline metadata include in `_name_form_row.html` new-name branch
    (#127 split metadata + parts behind a disclosure for existing rows only).
    """
    pid = person_only["pid"]
    resp = await client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "María García",
            "name_type": "legal",
            "is_canonical": "true",
            "visibility": "public",
            "locale": "es-MX",
            "script": "Latn",
            "sort_as": "García María",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    row = await _fetch_canonical_name(db, pid)
    assert row is not None
    assert row["visibility"] == "public"
    assert row["locale"] == "es-MX"
    assert row["script"] == "Latn"
    assert row["sort_as"] == "García María"


# --- _maybe_promote_sole_name exclusions (#308, CR round 1 finding 6) -------
# The delete-path promotion must agree with observation-path auto-promotion:
# a name that can never display must not claim the canonical slot.


async def _add_raw_name(db, pid, name, name_type, visibility):
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, FALSE)",
        nid,
        pid,
        name,
        name_type,
        visibility,
    )
    return nid


async def test_promote_sole_name_promotes_ordinary_name(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _add_raw_name(db, pid, "Jane Doe", "legal", "public")
    await _maybe_promote_sole_name(pid, db)
    assert await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_promote_sole_name_skips_deadname(db):
    """A deadname is invisible to the display view — promoting it strands the slot."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _add_raw_name(db, pid, "Old Name", "deadname", "legal_only")
    await _maybe_promote_sole_name(pid, db)
    assert not await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_promote_sole_name_skips_machine_readable(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _add_raw_name(db, pid, "YAMADA<<TARO", "mrz", "public")
    await _maybe_promote_sole_name(pid, db)
    assert not await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_promote_sole_name_skips_non_public_visibility(db):
    """visibility, not just name_type — a legal_only row cannot display either."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await _add_raw_name(db, pid, "Sealed Name", "legal", "legal_only")
    await _maybe_promote_sole_name(pid, db)
    assert not await db.fetchval("SELECT is_canonical FROM person_names WHERE person_id=$1", pid)


async def test_create_canonical_non_public_rejected(client, person_only, db):
    """#308: a canonical name must be public — flash + no write, not a 500.

    chk_person_canonical_is_public rejects the combination at the DB; the form
    validator catches it first so the admin sees which fields to change.
    """
    pid = person_only["pid"]
    resp = await client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Sealed Name",
            "name_type": "legal",
            "is_canonical": "true",
            "visibility": "legal_only",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    assert "HX-Trigger" in resp.headers
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", pid) == 0


async def test_delete_canonical_promotes_from_several_remaining_names(client, person_only, db):
    """Deleting the canonical must not strand a person with names left (CR5 #45).

    `_maybe_promote_sole_name` returned early unless exactly one name remained,
    so deleting the canonical of a three-name person left `display_name` NULL
    with two perfectly good public names present. Nothing repaired it until an
    observation happened to touch that person.
    """
    pid = person_only["pid"]
    canonical = await _add_raw_name(db, pid, "Alice Smith", "legal", "public")
    await db.execute("UPDATE person_names SET is_canonical=TRUE WHERE id=$1", canonical)
    await _add_raw_name(db, pid, "Alice", "preferred", "public")
    await _add_raw_name(db, pid, "A. Smith", "alias", "public")
    resp = await client.delete(f"/admin/people/{pid}/names/{canonical}/", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", pid) == 2
    # `preferred` outranks `alias` in the display ladder.
    assert (
        await db.fetchval("SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid)
        == "Alice"
    )


async def test_create_canonical_deadname_rejected(client, person_only, db):
    """#308 CR4: submitted visibility is not the visibility that lands.

    `trg_deadname_visibility` rewrites a deadname row to legal_only BEFORE
    INSERT, so `visibility=public` passes a validator that only inspects the
    submitted value and then violates chk_person_canonical_is_public — a 500 on
    the plain admin form, since `deadname` is in the name_type dropdown and
    visibility defaults to public. The validator has to reject on name_type.
    """
    pid = person_only["pid"]
    resp = await client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Old Name",
            "name_type": "deadname",
            "is_canonical": "true",
            "visibility": "public",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    assert "HX-Trigger" in resp.headers
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", pid) == 0


async def test_edit_canonical_with_omitted_visibility_rejected(client, person_only, db):
    """#308 CR4: an omitted visibility must validate against the stored value.

    `_update_name` skips a None visibility, so the row keeps its legal_only
    setting while is_canonical flips to TRUE — CHECK violation, 500. The
    validator returned early on `vis is None`, so nothing caught it.
    """
    pid = person_only["pid"]
    nid = await _add_raw_name(db, pid, "Sealed Name", "legal", "legal_only")
    resp = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={
            "name": "Sealed Name",
            "name_type": "legal",
            "is_canonical": "true",
            # visibility deliberately omitted
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    assert "HX-Trigger" in resp.headers
    row = await db.fetchrow("SELECT is_canonical, visibility FROM person_names WHERE id=$1", nid)
    assert not row["is_canonical"]
    assert row["visibility"] == "legal_only"


# ---------------------------------------------------------------------------
# Cite button citation count (#341)
# ---------------------------------------------------------------------------


async def test_name_read_row_cite_button_shows_count(client, db, person_and_name):
    pid, nid = person_and_name
    for url in ("https://example.com/a", "https://example.com/b"):
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id, url)"
            " VALUES ($1, 'person_name', $2, $3)",
            generate_id(),
            nid,
            url,
        )
    r = await client.get(f"/admin/people/{pid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f'<span id="cite-count-{nid}"> (2)</span>' in r.text


async def test_name_read_row_cite_button_plain_without_citations(client, person_and_name):
    pid, nid = person_and_name
    r = await client.get(f"/admin/people/{pid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f'<span id="cite-count-{nid}"></span>' in r.text


async def test_person_detail_name_row_cite_button_shows_count(client, db, person_and_name):
    """Batch path: the detail-page names table carries the count (no N+1)."""
    pid, nid = person_and_name
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'person_name', $2, 'https://example.com/a')",
        generate_id(),
        nid,
    )
    r = await client.get(f"/admin/people/{pid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    names_table = table_html(r.text, "names-table")
    assert f'<span id="cite-count-{nid}"> (1)</span>' in names_table


async def test_cite_drawer_create_refreshes_button_count_oob(client, db, person_and_name):
    """#341 CR1: in-drawer create OOB-refreshes the row's cite-count span."""
    _pid, nid = person_and_name
    r = await client.post(
        f"/admin/person-names/{nid}/citations/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.com/a", "title": "", "excerpt": "", "field_name": ""},
    )
    assert r.status_code == 200
    assert f'<span id="cite-count-{nid}" hx-swap-oob="true"> (1)</span>' in r.text


async def test_cite_drawer_delete_refreshes_button_count_oob(client, db, person_and_name):
    """#341 CR1: in-drawer delete OOB-clears the row's cite-count span."""
    _pid, nid = person_and_name
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, field_name, url)"
        " VALUES ($1, 'person_name', $2, 'name', 'https://example.com/a')",
        cid,
        nid,
    )
    r = await client.delete(f"/admin/person-names/{nid}/citations/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f'<span id="cite-count-{nid}" hx-swap-oob="true"></span>' in r.text
