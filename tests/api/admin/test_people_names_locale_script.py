"""Phase 2b Task 2 — locale / script / sort_as round-trip on person-name CRUD.

Mirrors the visibility round-trip pattern from Phase 2a Task 1. The
backend accepts the three new Form fields when supports_person_metadata=True
(person_names) and ignores them otherwise (org_names path).

FK violations on locale / script must surface as form errors (HTMX 200
with flash, non-HTMX 422), never as bare 500s.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id
from src.core.types import PERSON_NAME_TYPES

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


async def _fetch_metadata(pool, pid: str, nid: str) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT locale, script, sort_as FROM person_names WHERE id=$1 AND person_id=$2",
            nid,
            pid,
        )
        return dict(row) if row else {}


async def _fetch_new_name_id(pool, pid: str, name: str) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
            pid,
            name,
        )


# ---- Round-trip on create ------------------------------------------------


async def test_create_persists_locale(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Locale Test")
    assert (await _fetch_metadata(db_pool, pid, new_id))["locale"] == "en-US"


async def test_create_persists_script(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Script Test")
    assert (await _fetch_metadata(db_pool, pid, new_id))["script"] == "Latn"


async def test_create_persists_sort_as(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "van der Meer")
    assert (await _fetch_metadata(db_pool, pid, new_id))["sort_as"] == "Meer, van der"


async def test_create_persists_all_three(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Triple")
    md = await _fetch_metadata(db_pool, pid, new_id)
    assert md == {"locale": "en-US", "script": "Latn", "sort_as": "Triple, Test"}


async def test_create_empty_sort_as_stored_as_null(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Empty Sort")
    assert (await _fetch_metadata(db_pool, pid, new_id))["sort_as"] is None


async def test_create_whitespace_only_sort_as_stored_as_null(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "WS Sort")
    assert (await _fetch_metadata(db_pool, pid, new_id))["sort_as"] is None


async def test_create_strips_whitespace_from_sort_as(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Trim Sort")
    assert (await _fetch_metadata(db_pool, pid, new_id))["sort_as"] == "Trim, Test"


# ---- Round-trip on edit --------------------------------------------------


async def test_edit_persists_metadata(client, person_and_name, db_pool):
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
    md = await _fetch_metadata(db_pool, pid, nid)
    assert md == {"locale": "en-GB", "script": "Latn", "sort_as": "Original Name"}


async def test_edit_clears_sort_as_when_empty(client, person_and_name, db_pool):
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
    assert (await _fetch_metadata(db_pool, pid, nid))["sort_as"] == "Set Value"
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
    assert (await _fetch_metadata(db_pool, pid, nid))["sort_as"] is None


# ---- FK violation surfaces as form error (not 500) -----------------------


async def test_create_unregistered_locale_returns_form_error_htmx(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Bad Locale")
    assert new_id is None


async def test_create_unregistered_script_returns_form_error_htmx(client, person_and_name, db_pool):
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
    new_id = await _fetch_new_name_id(db_pool, pid, "Bad Script")
    assert new_id is None


async def test_edit_unregistered_locale_returns_form_error_htmx(client, person_and_name, db_pool):
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
    assert (await _fetch_metadata(db_pool, pid, nid))["locale"] is None


async def test_create_unregistered_locale_non_htmx_returns_422(client, person_and_name):
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


# ---- supports_person_metadata=False (org_names) ignores the new fields ----------


async def test_org_names_ignores_locale_script_sort_as(client, db_pool):
    """Posting these fields to org_names must not 500 and must not affect storage."""
    org_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

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
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", org_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---- All 12 name_types still accepted (Task 1 Phase 2a regression) -------


@pytest.mark.parametrize("name_type", PERSON_NAME_TYPES)
async def test_create_accepts_all_name_types_with_metadata(client, person_and_name, name_type):
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
