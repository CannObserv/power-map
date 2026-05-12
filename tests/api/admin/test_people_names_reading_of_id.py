"""Phase 2c Task 1 — reading_of_id round-trip + cross-person rejection.

reading_of_id is a self-FK on person_names. When supports_metadata=True,
the person-name CRUD accepts it on create + edit. Cross-person links and
self-links must be rejected as form errors (HTMX flash + non-HTMX 422),
never bare 500s.
"""

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
async def two_people_with_visuals(db_pool):
    """Two people, each with one visual canonical row."""
    pid_a = generate_id()
    pid_b = generate_id()
    nid_a = generate_id()
    nid_b = generate_id()

    async with db_pool.acquire() as conn:
        for pid, nid, name in (
            (pid_a, nid_a, "Visual A"),
            (pid_b, nid_b, "Visual B"),
        ):
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
                nid,
                pid,
                name,
            )

    yield {"pid_a": pid_a, "pid_b": pid_b, "nid_a": nid_a, "nid_b": nid_b}

    async with db_pool.acquire() as conn:
        for pid in (pid_a, pid_b):
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def _fetch_reading_of(pool, pid: str, nid: str) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT reading_of_id FROM person_names WHERE id=$1 AND person_id=$2",
            nid,
            pid,
        )


async def _fetch_new_name_id(pool, pid: str, name: str) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
            pid,
            name,
        )


# ---- Round-trip on create --------------------------------------------


async def test_create_persists_reading_of_id(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "visual a",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_a"],
        },
    )
    assert r.status_code == 200, r.text
    new_id = await _fetch_new_name_id(db_pool, f["pid_a"], "visual a")
    assert new_id is not None
    assert await _fetch_reading_of(db_pool, f["pid_a"], new_id) == f["nid_a"]


async def test_create_empty_reading_of_id_stored_as_null(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "free reading",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": "",
        },
    )
    assert r.status_code == 200
    new_id = await _fetch_new_name_id(db_pool, f["pid_a"], "free reading")
    assert await _fetch_reading_of(db_pool, f["pid_a"], new_id) is None


async def test_create_omitted_reading_of_id_stored_as_null(
    client, two_people_with_visuals, db_pool
):
    """Field omitted entirely (not even in the form) should be NULL."""
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "no link",
            "name_type": "reading",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    new_id = await _fetch_new_name_id(db_pool, f["pid_a"], "no link")
    assert await _fetch_reading_of(db_pool, f["pid_a"], new_id) is None


# ---- Round-trip on edit ----------------------------------------------


async def test_edit_persists_reading_of_id(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    # Insert a reading row first.
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "to-edit",
            "name_type": "reading",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    nid_to_edit = await _fetch_new_name_id(db_pool, f["pid_a"], "to-edit")
    # Now set its reading_of_id via edit.
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/{nid_to_edit}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "to-edit",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": f["nid_a"],
        },
    )
    assert r2.status_code == 200
    assert await _fetch_reading_of(db_pool, f["pid_a"], nid_to_edit) == f["nid_a"]


async def test_edit_clears_reading_of_id_when_blank(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    # Insert with a link.
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "linked",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_a"],
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db_pool, f["pid_a"], "linked")
    assert await _fetch_reading_of(db_pool, f["pid_a"], nid) == f["nid_a"]
    # Now clear it.
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "linked",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": "",
        },
    )
    assert r2.status_code == 200
    assert await _fetch_reading_of(db_pool, f["pid_a"], nid) is None


# ---- Cross-person rejection ------------------------------------------


async def test_create_cross_person_reading_of_id_rejected(client, two_people_with_visuals, db_pool):
    """Pointing at another person's row must surface as a form error."""
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "cross-person",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_b"],
        },
    )
    # HTMX → 200 + flash, never 500.
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    # Row must not have been created.
    assert await _fetch_new_name_id(db_pool, f["pid_a"], "cross-person") is None


async def test_edit_cross_person_reading_of_id_rejected(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    # Create a clean reading row first.
    client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "edit-cross",
            "name_type": "reading",
            "is_canonical": "",
        },
    )
    nid = await _fetch_new_name_id(db_pool, f["pid_a"], "edit-cross")
    # Edit pointing at the other person's row.
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "edit-cross",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": f["nid_b"],
        },
    )
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    # Row reading_of_id remains NULL.
    assert await _fetch_reading_of(db_pool, f["pid_a"], nid) is None


async def test_create_unknown_reading_of_id_rejected(client, two_people_with_visuals, db_pool):
    """A `reading_of_id` that doesn't reference any row at all → form error."""
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "ghost",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": generate_id(),  # not a real row
        },
    )
    assert r.status_code == 200, r.text
    assert "showFlash" in r.headers.get("HX-Trigger", "")
    assert await _fetch_new_name_id(db_pool, f["pid_a"], "ghost") is None


async def test_create_cross_person_non_htmx_returns_422(client, two_people_with_visuals):
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "name": "cross-person nonhtmx",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_b"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 422


# ---- Visual-target + no-self-reference enforcement -------------------


async def test_create_reading_of_id_pointing_at_reading_row_rejected(
    client, two_people_with_visuals, db_pool
):
    """A reading/romanization/mrz row is never a valid `reading_of_id` target.

    Typeahead filters them out; the POST validator must too — otherwise a
    user can construct chains (A→B→A) by bypassing the typeahead.
    """
    f = two_people_with_visuals
    # First create a romanization row on person A pointing at the legal row.
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "intermediate",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_a"],
        },
    )
    assert r.status_code == 200
    intermediate_nid = await _fetch_new_name_id(db_pool, f["pid_a"], "intermediate")
    # Now try to create another reading row pointing at the romanization row.
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "chained",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": intermediate_nid,
        },
    )
    assert r2.status_code == 200, r2.text
    assert "HX-Trigger" in r2.headers
    assert "visual" in r2.headers["HX-Trigger"].lower()
    # No row written.
    assert await _fetch_new_name_id(db_pool, f["pid_a"], "chained") is None


async def test_create_reading_of_id_points_at_reading_non_htmx_returns_422(
    client, two_people_with_visuals, db_pool
):
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "intermediate2",
            "name_type": "romanization",
            "is_canonical": "",
            "reading_of_id": f["nid_a"],
        },
    )
    assert r.status_code == 200
    intermediate_nid = await _fetch_new_name_id(db_pool, f["pid_a"], "intermediate2")
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=AUTH_HEADERS,
        data={
            "name": "chained2",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": intermediate_nid,
        },
    )
    assert r2.status_code == 422


async def test_edit_self_reference_rejected(client, two_people_with_visuals, db_pool):
    """Setting `reading_of_id` to the row's OWN id must be rejected."""
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "self-ref-target",
            "name_type": "reading",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db_pool, f["pid_a"], "self-ref-target")
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "self-ref-target",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": nid,
        },
    )
    assert r2.status_code == 200
    assert "HX-Trigger" in r2.headers
    assert "itself" in r2.headers["HX-Trigger"].lower()
    assert await _fetch_reading_of(db_pool, f["pid_a"], nid) is None


async def test_edit_self_reference_non_htmx_returns_422(client, two_people_with_visuals, db_pool):
    f = two_people_with_visuals
    r = client.post(
        f"/admin/people/{f['pid_a']}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "self-ref-target2",
            "name_type": "reading",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db_pool, f["pid_a"], "self-ref-target2")
    r2 = client.post(
        f"/admin/people/{f['pid_a']}/names/{nid}/edit-row/",
        headers=AUTH_HEADERS,
        data={
            "name": "self-ref-target2",
            "name_type": "reading",
            "is_canonical": "",
            "reading_of_id": nid,
        },
    )
    assert r2.status_code == 422


# ---- Org-side ignores reading_of_id ---------------------------------


async def test_org_names_ignores_reading_of_id(client, two_people_with_visuals, db_pool):
    """Posting reading_of_id to org_names must not 500 and must not affect storage."""
    org_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    try:
        r = client.post(
            f"/admin/orgs/{org_id}/names/",
            headers=HTMX_HEADERS,
            data={
                "name": "Org",
                "name_type": "dba",
                "is_canonical": "",
                # Ignored: organization_names has no reading_of_id column.
                "reading_of_id": "irrelevant",
            },
        )
        assert r.status_code == 200, r.text
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", org_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", org_id)
