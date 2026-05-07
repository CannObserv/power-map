"""Integration tests for person names CRUD."""

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def person_and_name():
    dsn = _dsn()
    pid, nid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO people (id) VALUES ($1)",
                pid,
            )
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Original Name', 'legal', TRUE)",
                nid,
                pid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid, nid
    asyncio.run(teardown())


def test_names_create(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "Former Name", "name_type": "former", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "Former Name" in r.text


def test_names_read_row(client, person_and_name):
    pid, nid = person_and_name
    r = client.get(f"/admin/people/{pid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


def test_names_edit_row_get(client, person_and_name):
    pid, nid = person_and_name
    r = client.get(f"/admin/people/{pid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


def test_names_update(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_names_update_returns_success_flash(client, person_and_name):
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


def test_names_delete(client, person_and_name):
    dsn = _dsn()
    pid, _ = person_and_name
    nid2 = generate_id()

    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Former Name', 'former', FALSE)",
                nid2,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add())
    r = client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_names_delete_last_blocked(client, person_and_name):
    """Deleting the last name must be blocked with an error flash."""
    pid, nid = person_and_name
    r = client.delete(f"/admin/people/{pid}/names/{nid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    dsn = _dsn()

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow("SELECT id FROM person_names WHERE id=$1", nid)
        finally:
            await conn.close()

    assert asyncio.run(check()) is not None


def test_names_delete_last_non_htmx_returns_409(client, person_and_name):
    pid, nid = person_and_name
    r = client.delete(f"/admin/people/{pid}/names/{nid}/", headers=AUTH_HEADERS)
    assert r.status_code == 409


def test_name_edit_sole_uncanonical_is_blocked(client, person_and_name):
    """Unchecking canonical on the only name must be blocked with an error flash."""
    dsn = _dsn()
    pid, nid = person_and_name

    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT is_canonical FROM person_names WHERE id=$1", nid
            )
            return row["is_canonical"]
        finally:
            await conn.close()

    assert asyncio.run(check()) is True, "sole name must remain canonical after blocked edit"


def test_name_edit_uncanonical_with_multiple_names_blocked(client, person_and_name):
    """Unchecking canonical on the only canonical name (when others exist) must be blocked."""
    dsn = _dsn()
    pid, canonical_nid = person_and_name
    other_nid = generate_id()

    async def add_second():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Former Name', 'former', FALSE)",
                other_nid,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add_second())
    r = client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT is_canonical FROM person_names WHERE id=$1", canonical_nid
            )
            return row["is_canonical"]
        finally:
            await conn.close()

    assert asyncio.run(check()) is True, "canonical must not be changed"


def test_name_edit_uncanonical_non_htmx_redirects(client, person_and_name):
    """Non-HTMX path: unchecking canonical on only canonical (multiple names) must redirect."""
    dsn = _dsn()
    pid, canonical_nid = person_and_name
    other_nid = generate_id()

    async def add_second():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Former Name', 'former', FALSE)",
                other_nid,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add_second())
    r = client.post(
        f"/admin/people/{pid}/names/{canonical_nid}/edit-row/",
        headers=AUTH_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT is_canonical FROM person_names WHERE id=$1", canonical_nid
            )
            return row["is_canonical"]
        finally:
            await conn.close()

    assert asyncio.run(check()) is True, "canonical must not be changed"


def test_name_edit_non_canonical_row_can_stay_non_canonical(client, person_and_name):
    """Editing a non-canonical name without checking canonical must succeed."""
    dsn = _dsn()
    pid, _ = person_and_name
    other_nid = generate_id()

    async def add_second():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Former Name', 'former', FALSE)",
                other_nid,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add_second())
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


def test_names_new_row_returns_form(client, person_and_name):
    pid, _ = person_and_name
    r = client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_names_form_row_canonical_toggle_has_aria_label(client, person_and_name):
    pid, _ = person_and_name
    r = client.get(f"/admin/people/{pid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Canonical"' in r.text


def test_names_edit_returns_tbody(client, person_and_name):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    dsn = _dsn()
    pid, nid = person_and_name
    nid2 = generate_id()

    async def add_second():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Second Name', 'former', FALSE)",
                nid2,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add_second())
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Original Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert f'id="name-row-{nid}"' in r.text
    assert f'id="name-row-{nid2}"' in r.text
    assert "<table" not in r.text


def test_names_create_returns_update_person_header(client, person_and_name):
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


def test_names_update_returns_update_person_header_with_new_display(client, person_and_name):
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


def test_names_delete_returns_update_person_header(client, person_and_name):
    """Deleting a non-canonical name must emit updatePersonHeader."""
    dsn = _dsn()
    pid, _ = person_and_name
    nid2 = generate_id()

    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Former Name', 'former', FALSE)",
                nid2,
                pid,
            )
        finally:
            await conn.close()

    asyncio.run(add())
    r = client.delete(f"/admin/people/{pid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "updatePersonHeader" in trigger


# ---------------------------------------------------------------------------
# Combined name + parts payload (Issue #127 Task D)
# ---------------------------------------------------------------------------


def test_names_create_accepts_combined_parts_payload(client, person_and_name):
    """Issue #127: POST / accepts parts fields and seeds person_name_parts."""
    dsn = _dsn()
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

    async def fetch():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT pn.id AS nid, pnp.given_names, pnp.family_names,"
                " pnp.primary_identifier"
                " FROM person_names pn"
                " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
                " WHERE pn.person_id=$1 AND pn.name='Ada Lovelace'",
                pid,
            )
        finally:
            await conn.close()

    row = asyncio.run(fetch())
    assert row is not None
    assert row["given_names"] == ["Ada"]
    assert row["family_names"] == ["Lovelace"]
    assert row["primary_identifier"] == "family"


def test_names_update_accepts_combined_parts_payload(client, person_and_name):
    """Issue #127: POST /edit-row/ updates name AND upserts parts in one transaction."""
    dsn = _dsn()
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

    async def fetch():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT pn.name, pnp.given_names, pnp.family_names"
                " FROM person_names pn"
                " LEFT JOIN person_name_parts pnp ON pnp.person_name_id = pn.id"
                " WHERE pn.id=$1",
                nid,
            )
        finally:
            await conn.close()

    row = asyncio.run(fetch())
    assert row["name"] == "Renamed"
    assert row["given_names"] == ["Re"]
    assert row["family_names"] == ["Named"]
