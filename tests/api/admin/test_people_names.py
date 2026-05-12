"""Integration tests for person names CRUD."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def person_and_name(db_pool):
    pid, nid = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, 'Original Name', 'legal', TRUE)",
            nid,
            pid,
        )

    yield pid, nid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_only(db_pool):
    """One person row, no names yet (for create-flow tests)."""
    pid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)

    yield {"pid": pid}

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def _fetch_canonical_name(pool, person_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, name_type, is_canonical, visibility,"
            " locale, script, sort_as"
            " FROM person_names WHERE person_id=$1 AND is_canonical=TRUE",
            person_id,
        )
        return dict(row) if row else None


async def _insert_second_name(
    pool, person_id: str, name_id: str, name: str = "Former Name", name_type: str = "former"
) -> None:
    """Insert a second (non-canonical) name for an existing person."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, FALSE)",
            name_id,
            person_id,
            name,
            name_type,
        )


async def _fetch_is_canonical(pool, name_id: str) -> bool | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_canonical FROM person_names WHERE id=$1", name_id)
        return row["is_canonical"] if row else None


async def test_names_create(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "Former Name" in r.text


async def test_names_read_row(client, person_and_name):
    pid, nid = person_and_name
    r = client.get(f"/admin/people/{pid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


async def test_names_edit_row_get(client, person_and_name):
    pid, nid = person_and_name
    r = client.get(f"/admin/people/{pid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


async def test_names_update(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


async def test_names_update_returns_success_flash(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Updated Name" in trigger["showFlash"]["body"]


async def test_names_delete(client, person_and_name, db_pool):
    pid, _ = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db_pool, pid, nid2)

    r = client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_names_delete_last_blocked(client, person_and_name, db_pool):
    """Deleting the last name must be blocked with an error flash."""
    pid, nid = person_and_name
    r = client.delete(f"/admin/people/{pid}/names/{nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM person_names WHERE id=$1", nid)
    assert row is not None


async def test_names_delete_last_non_htmx_returns_409(client, person_and_name):
    pid, nid = person_and_name
    r = client.delete(f"/admin/people/{pid}/names/{nid}/", headers=AUTH_HEADERS)
    assert r.status_code == 409


async def test_name_edit_sole_uncanonical_is_blocked(client, person_and_name, db_pool):
    """Unchecking canonical on the only name must be blocked with an error flash."""
    pid, nid = person_and_name

    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"

    is_canonical = await _fetch_is_canonical(db_pool, nid)
    assert is_canonical is True, "sole name must remain canonical after blocked edit"


async def test_name_edit_uncanonical_with_multiple_names_blocked(client, person_and_name, db_pool):
    """Unchecking canonical on the only canonical name (when others exist) must be blocked."""
    pid, canonical_nid = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db_pool, pid, other_nid)

    r = client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"

    is_canonical = await _fetch_is_canonical(db_pool, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_uncanonical_non_htmx_redirects(client, person_and_name, db_pool):
    """Non-HTMX path: unchecking canonical on only canonical (multiple names) must redirect."""
    pid, canonical_nid = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db_pool, pid, other_nid)

    r = client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=AUTH_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    is_canonical = await _fetch_is_canonical(db_pool, canonical_nid)
    assert is_canonical is True, "canonical must not be changed"


async def test_name_edit_non_canonical_row_can_stay_non_canonical(client, person_and_name, db_pool):
    """Editing a non-canonical name without checking canonical must succeed."""
    pid, _ = person_and_name
    other_nid = generate_id()
    await _insert_second_name(db_pool, pid, other_nid)

    r = client.post(
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
    r = client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_names_form_row_canonical_toggle_has_aria_label(client, person_and_name):
    pid, _ = person_and_name
    r = client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Canonical"' in r.text


async def test_names_edit_returns_tbody(client, person_and_name, db_pool):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    pid, nid = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db_pool, pid, nid2, name="Second Name")

    r = client.post(
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
    r = client.post(
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
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Renamed Person", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


async def test_names_delete_returns_update_person_header(client, person_and_name, db_pool):
    """Deleting a non-canonical name must emit updatePersonHeader."""
    pid, _ = person_and_name
    nid2 = generate_id()
    await _insert_second_name(db_pool, pid, nid2)

    r = client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


# ---------------------------------------------------------------------------
# Combined name + parts payload (Issue #127 Task D)
# ---------------------------------------------------------------------------


async def test_names_create_accepts_combined_parts_payload(client, person_and_name, db_pool):
    """Issue #127: POST / accepts parts fields and seeds person_name_parts."""
    pid, _ = person_and_name
    r = client.post(
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

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
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


async def test_names_update_accepts_combined_parts_payload(client, person_and_name, db_pool):
    """Issue #127: POST /edit-row/ updates name AND upserts parts in one transaction."""
    pid, nid = person_and_name
    r = client.post(
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

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
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


async def test_create_new_name_persists_metadata_fields(client, person_only, db_pool):
    """Issue #130: POSTing the new-name form with metadata fields must write
    visibility/locale/script/sort_as to person_names. Regression coverage for
    the inline metadata include in `_name_form_row.html` new-name branch
    (#127 split metadata + parts behind a disclosure for existing rows only).
    """
    pid = person_only["pid"]
    resp = client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "María García",
            "name_type": "legal",
            "is_canonical": "true",
            "visibility": "legal_only",
            "locale": "es-MX",
            "script": "Latn",
            "sort_as": "García María",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    row = await _fetch_canonical_name(db_pool, pid)
    assert row is not None
    assert row["visibility"] == "legal_only"
    assert row["locale"] == "es-MX"
    assert row["script"] == "Latn"
    assert row["sort_as"] == "García María"
