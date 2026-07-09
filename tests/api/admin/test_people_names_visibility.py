"""Phase 2a backend tests — person-name visibility metadata.

Covers the `supports_person_metadata=True` flag on `make_names_router`:
- visibility round-trip (create + edit accept the Form field, persist to DB)
- every `name_type` in `src.core.types.PERSON_NAME_TYPES` is accepted
- deadname coercion via DB trigger (public → legal_only on insert/update)
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.types import PERSON_NAME_TYPES

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


async def _fetch_visibility(db, pid: str, nid: str) -> str:
    return await db.fetchval(
        "SELECT visibility FROM person_names WHERE id=$1 AND person_id=$2",
        nid,
        pid,
    )


async def _fetch_name_type(db, pid: str, nid: str) -> str:
    return await db.fetchval(
        "SELECT name_type FROM person_names WHERE id=$1 AND person_id=$2",
        nid,
        pid,
    )


async def _fetch_new_name_id(db, pid: str, name: str) -> str:
    return await db.fetchval(
        "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
        pid,
        name,
    )


# ---- visibility round-trip on create -------------------------------------


async def test_create_persists_visibility_legal_only(client, person_and_name, db):
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Legal Only Name",
            "name_type": "legal",
            "is_canonical": "",
            "visibility": "legal_only",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db, pid, "Legal Only Name")
    assert await _fetch_visibility(db, pid, nid) == "legal_only"


async def test_create_persists_visibility_hidden(client, person_and_name, db):
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Hidden Name",
            "name_type": "alias",
            "is_canonical": "",
            "visibility": "hidden",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db, pid, "Hidden Name")
    assert await _fetch_visibility(db, pid, nid) == "hidden"


async def test_create_defaults_visibility_to_public(client, person_and_name, db):
    """Visibility omitted → DB default 'public' applies."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Public Default",
            "name_type": "preferred",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db, pid, "Public Default")
    assert await _fetch_visibility(db, pid, nid) == "public"


# ---- visibility round-trip on edit ---------------------------------------


async def test_edit_persists_visibility(client, person_and_name, db):
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "visibility": "legal_only",
        },
    )
    assert r.status_code == 200
    assert await _fetch_visibility(db, pid, nid) == "legal_only"


# ---- invalid visibility rejected with 422 --------------------------------


async def test_create_rejects_invalid_visibility(client, person_and_name):
    """Out-of-range visibility values return 422 (Pydantic Literal validation)."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Bad Visibility",
            "name_type": "alias",
            "is_canonical": "",
            "visibility": "banana",
        },
    )
    assert r.status_code == 422


async def test_edit_rejects_invalid_visibility(client, person_and_name):
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "legal",
            "is_canonical": "true",
            "visibility": "banana",
        },
    )
    assert r.status_code == 422


# ---- invalid name_type rejected by _validate_name_type -------------------
#
# Defense in depth above the DB CHECK constraint: handler-level
# validation against the configured ``name_types`` tuple returns a
# friendly 422 (non-HTMX) or 200 + flash (HTMX) instead of bubbling a
# raw asyncpg.CheckViolationError.


async def test_create_rejects_invalid_name_type_non_htmx(client, person_and_name):
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=AUTH_HEADERS,  # no HX-Request — non-HTMX path
        data={
            "name": "Bad Type",
            "name_type": "nickname",  # not in PERSON_NAME_TYPES
            "is_canonical": "",
        },
    )
    assert r.status_code == 422
    assert "Invalid name_type" in r.text
    assert "nickname" in r.text


async def test_create_rejects_invalid_name_type_htmx(client, person_and_name):
    """HTMX path returns 200 with HX-Trigger flash, not 422 — admin
    convention for form errors so the page can render the flash."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Bad Type",
            "name_type": "nickname",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert "Invalid name_type" in r.headers["HX-Trigger"]


async def test_edit_rejects_invalid_name_type_htmx(client, person_and_name):
    pid, nid = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "name": "Original Name",
            "name_type": "totally_made_up",
            "is_canonical": "true",
        },
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers
    assert "Invalid name_type" in r.headers["HX-Trigger"]


async def test_create_path_404_wins_over_body_422_for_invalid_name_type(client):
    """Path-level errors (entity not found → 404) must win over
    body-level errors (invalid name_type → 422) when both apply.

    Regression guard for the round-2 ordering fix: ``_validate_name_type``
    runs after ``_get_entity_or_404`` so a typoed name_type on a
    non-existent person surfaces the more informative 404, not the 422.
    """
    r = await client.post(
        "/admin/people/p_does_not_exist/names/",
        headers=AUTH_HEADERS,  # non-HTMX → real status code, not flash
        data={
            "name": "x",
            "name_type": "not_a_real_type",  # also invalid (would be 422)
            "is_canonical": "",
        },
    )
    assert r.status_code == 404


async def test_edit_path_404_wins_over_body_422_for_invalid_name_type(client):
    """Same precedence on the edit-row handler: a missing name_id
    surfaces 404 even when the body's name_type would otherwise 422."""
    r = await client.post(
        "/admin/people/p_does_not_exist/names/n_does_not_exist/edit-row/",
        headers=AUTH_HEADERS,
        data={
            "name": "x",
            "name_type": "not_a_real_type",
            "is_canonical": "",
        },
    )
    assert r.status_code == 404


# ---- expanded name_type values -------------------------------------------


@pytest.mark.parametrize("name_type", PERSON_NAME_TYPES)
async def test_create_accepts_all_name_types(client, person_and_name, db, name_type):
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": f"Test {name_type}",
            "name_type": name_type,
            "is_canonical": "",
        },
    )
    assert r.status_code == 200, r.text
    nid = await _fetch_new_name_id(db, pid, f"Test {name_type}")
    assert await _fetch_name_type(db, pid, nid) == name_type


# ---- deadname → legal_only coercion via trg_deadname_visibility ----------


async def test_create_deadname_coerces_public_to_legal_only(client, person_and_name, db):
    """Public visibility on deadname row → DB trigger downgrades to legal_only."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Pre-Transition Name",
            "name_type": "deadname",
            "is_canonical": "",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db, pid, "Pre-Transition Name")
    assert await _fetch_visibility(db, pid, nid) == "legal_only"


async def test_create_deadname_preserves_explicit_hidden(client, person_and_name, db):
    """Explicit 'hidden' on deadname row is preserved (trigger only downgrades public)."""
    pid, _ = person_and_name
    r = await client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Hidden Deadname",
            "name_type": "deadname",
            "is_canonical": "",
            "visibility": "hidden",
        },
    )
    assert r.status_code == 200
    nid = await _fetch_new_name_id(db, pid, "Hidden Deadname")
    assert await _fetch_visibility(db, pid, nid) == "hidden"
