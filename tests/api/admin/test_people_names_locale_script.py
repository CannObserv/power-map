"""Phase 2b Task 2 — locale / script / sort_as round-trip on person-name CRUD.

Mirrors the visibility round-trip pattern from Phase 2a Task 1. The
backend accepts the three new Form fields when supports_metadata=True
(person_names) and ignores them otherwise (org_names path).

FK violations on locale / script must surface as form errors (HTMX 200
with flash, non-HTMX 422), never as bare 500s.
"""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def person_and_name():
    dsn = _dsn()
    pid, nid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Original Name', 'legal', TRUE)",
                nid, pid,
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


async def _fetch_metadata(pid: str, nid: str) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT locale, script, sort_as FROM person_names"
            " WHERE id=$1 AND person_id=$2",
            nid, pid,
        )
        return dict(row) if row else {}
    finally:
        await conn.close()


async def _fetch_new_name_id(pid: str, name: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
            pid, name,
        )
    finally:
        await conn.close()


# ---- Round-trip on create ------------------------------------------------


def test_create_persists_locale(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Locale Test",
            "name_type": "legal",
            "is_canonical": "",
            "locale": "en-US",
        },
    )
    assert r.status_code == 200, r.text
    new_id = asyncio.run(_fetch_new_name_id(pid, "Locale Test"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["locale"] == "en-US"


def test_create_persists_script(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Script Test",
            "name_type": "legal",
            "is_canonical": "",
            "script": "Latn",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "Script Test"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["script"] == "Latn"


def test_create_persists_sort_as(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "van der Meer",
            "name_type": "legal",
            "is_canonical": "",
            "sort_as": "Meer, van der",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "van der Meer"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["sort_as"] == "Meer, van der"


def test_create_persists_all_three(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Triple",
            "name_type": "legal",
            "is_canonical": "",
            "locale": "en-US",
            "script": "Latn",
            "sort_as": "Triple, Test",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "Triple"))
    md = asyncio.run(_fetch_metadata(pid, new_id))
    assert md == {"locale": "en-US", "script": "Latn", "sort_as": "Triple, Test"}


def test_create_empty_sort_as_stored_as_null(client, person_and_name):
    """Empty-string sort_as should become NULL, not '' — keeps sort fallback clean."""
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Empty Sort",
            "name_type": "legal",
            "is_canonical": "",
            "sort_as": "",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "Empty Sort"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["sort_as"] is None


def test_create_whitespace_only_sort_as_stored_as_null(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "WS Sort",
            "name_type": "legal",
            "is_canonical": "",
            "sort_as": "   ",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "WS Sort"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["sort_as"] is None


def test_create_strips_whitespace_from_sort_as(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Trim Sort",
            "name_type": "legal",
            "is_canonical": "",
            "sort_as": "  Trim, Test  ",
        },
    )
    assert r.status_code == 200
    new_id = asyncio.run(_fetch_new_name_id(pid, "Trim Sort"))
    assert asyncio.run(_fetch_metadata(pid, new_id))["sort_as"] == "Trim, Test"


# ---- Round-trip on edit --------------------------------------------------


def test_edit_persists_metadata(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "locale": "en-GB",
            "script": "Latn",
            "sort_as": "Original Name",
        },
    )
    assert r.status_code == 200
    md = asyncio.run(_fetch_metadata(pid, nid))
    assert md == {"locale": "en-GB", "script": "Latn", "sort_as": "Original Name"}


def test_edit_clears_sort_as_when_empty(client, person_and_name):
    """Editing with sort_as='' should NULL it, allowing fallback to name."""
    pid, nid = person_and_name
    # First set it.
    client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "sort_as": "Set Value",
        },
    )
    assert asyncio.run(_fetch_metadata(pid, nid))["sort_as"] == "Set Value"
    # Now clear it.
    client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "sort_as": "",
        },
    )
    assert asyncio.run(_fetch_metadata(pid, nid))["sort_as"] is None


# ---- FK violation surfaces as form error (not 500) -----------------------


def test_create_unregistered_locale_returns_form_error_htmx(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Bad Locale",
            "name_type": "legal",
            "is_canonical": "",
            "locale": "xx-XX",
        },
    )
    # HTMX path: 200 with flash trigger; never 500.
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    # No row should have been inserted.
    new_id = asyncio.run(_fetch_new_name_id(pid, "Bad Locale"))
    assert new_id is None


def test_create_unregistered_script_returns_form_error_htmx(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Bad Script",
            "name_type": "legal",
            "is_canonical": "",
            "script": "Xxxx",
        },
    )
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    new_id = asyncio.run(_fetch_new_name_id(pid, "Bad Script"))
    assert new_id is None


def test_edit_unregistered_locale_returns_form_error_htmx(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "locale": "yy-YY",
        },
    )
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    # Row unchanged.
    assert asyncio.run(_fetch_metadata(pid, nid))["locale"] is None


def test_create_unregistered_locale_non_htmx_returns_422(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "name": "Bad Locale NonHTMX",
            "name_type": "legal",
            "is_canonical": "",
            "locale": "zz-ZZ",
        },
        follow_redirects=False,
    )
    assert r.status_code == 422


# ---- supports_metadata=False (org_names) ignores the new fields ----------


def test_org_names_ignores_locale_script_sort_as(client):
    """Posting these fields to org_names must not 500 and must not affect storage."""
    dsn = _dsn()
    org_id = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id) VALUES ($1)", org_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", org_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        r = client.post(
            f"/admin/orgs/{org_id}/names/",
            headers=HTMX_HEADERS,
            data={
                "name": "Org Name",
                "name_type": "dba",
                "is_canonical": "",
                # All three are ignored at the gate; org schema lacks the columns.
                "locale": "en-US",
                "script": "Latn",
                "sort_as": "Some Sort",
            },
        )
        assert r.status_code == 200, r.text
    finally:
        asyncio.run(teardown())


# ---- All 12 name_types still accepted (Task 1 Phase 2a regression) -------


@pytest.mark.parametrize(
    "name_type",
    [
        "legal", "preferred", "alias", "former", "initials",
        "maiden", "religious", "stage", "deadname",
        "reading", "romanization", "mrz",
    ],
)
def test_create_accepts_all_name_types_with_metadata(client, person_and_name, name_type):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": f"NT {name_type}",
            "name_type": name_type,
            "is_canonical": "",
            "locale": "en-US",
            "script": "Latn",
            "sort_as": f"NT {name_type}",
        },
    )
    assert r.status_code == 200, r.text
