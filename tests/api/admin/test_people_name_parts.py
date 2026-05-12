"""Integration tests for `person_name_parts` upsert/delete via the unified
`/edit-row/` and `/` (create) handlers (#127).

The standalone `/parts/` and `/parts/delete/` routes were removed in #127.
The parts upsert/delete logic now runs inside the same transaction as the
name update / insert, gated by `supports_person_metadata=True`. Clearing
every parts field on Save deletes the existing row (semantic flip from
the old "all-empty is no-op" behavior).

Form encoding: arrays repeat (`given_names=María&given_names=José`).
Caps: 5 elements per array; empty strings filtered before INSERT.
`primary_identifier` allowlist: family / given / patronymic / mononym / blank.
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
async def person_with_legal_name(db_pool):
    """One person + one canonical legal name (no parts row)."""
    pid = generate_id()
    nid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, visibility)"
            " VALUES ($1, $2, 'María José García López', 'legal', TRUE, 'public')",
            nid,
            pid,
        )

    yield {"pid": pid, "nid": nid, "name": "María José García López"}

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_with_parts(db_pool):
    """One person + canonical name + a pre-seeded `person_name_parts` row."""
    pid = generate_id()
    nid = generate_id()
    name = "Ada Lovelace"

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, visibility)"
            " VALUES ($1, $2, $3, 'legal', TRUE, 'public')",
            nid,
            pid,
            name,
        )
        await conn.execute(
            "INSERT INTO person_name_parts"
            " (person_name_id, given_names, family_names, primary_identifier)"
            " VALUES ($1, $2, $3, $4)",
            nid,
            ["Ada"],
            ["Lovelace"],
            "family",
        )

    yield {"pid": pid, "nid": nid, "name": name}

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


async def _fetch_parts(pool, name_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT given_names, family_names, additional_names,"
            " honorific_prefix, honorific_suffix, primary_identifier"
            " FROM person_name_parts WHERE person_name_id=$1",
            name_id,
        )
        return dict(row) if row else None


async def _fetch_name_row(pool, name_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, person_id, name, name_type, is_canonical FROM person_names WHERE id=$1",
            name_id,
        )
        return dict(row) if row else None


async def _fetch_canonical_name(pool, person_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, name_type, is_canonical"
            " FROM person_names WHERE person_id=$1 AND is_canonical=TRUE",
            person_id,
        )
        return dict(row) if row else None


# ---- /edit-row/ — combined name + parts upsert ------------------------------


async def test_edit_row_post_creates_parts_row_alongside_name_update(
    client, person_with_legal_name, db_pool
):
    """Issue #127: a single POST to /edit-row/ updates the name AND upserts
    the parts row in one transaction."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": "María José García López",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["María", "José"],
            "family_names": ["García", "López"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = await _fetch_parts(db_pool, f["nid"])
    assert parts is not None
    assert parts["given_names"] == ["María", "José"]
    assert parts["family_names"] == ["García", "López"]
    assert parts["primary_identifier"] == "family"


async def test_edit_row_post_persists_honorifics(client, person_with_legal_name, db_pool):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Ada"],
            "honorific_prefix": "Dr.",
            "honorific_suffix": "FRS",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = await _fetch_parts(db_pool, f["nid"])
    assert parts["honorific_prefix"] == "Dr."
    assert parts["honorific_suffix"] == "FRS"


async def test_edit_row_post_filters_empty_string_array_entries(
    client, person_with_legal_name, db_pool
):
    """Empty strings in repeating fields are dropped before INSERT."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["María", "", "  ", "José"],
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = await _fetch_parts(db_pool, f["nid"])
    assert parts["given_names"] == ["María", "José"]


async def test_edit_row_post_updates_existing_parts_row(client, person_with_parts, db_pool):
    """Second POST replaces — no UniqueViolation, full replacement of arrays."""
    f = person_with_parts
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["María", "José"],
            "family_names": ["García"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = await _fetch_parts(db_pool, f["nid"])
    assert parts["given_names"] == ["María", "José"]
    assert parts["family_names"] == ["García"]
    assert parts["primary_identifier"] == "family"


async def test_edit_row_post_with_all_empty_parts_deletes_existing_parts_row(
    client, person_with_parts, db_pool
):
    """Issue #127: clearing every parts field on Save deletes the parts row."""
    f = person_with_parts
    # Pre-condition: parts row exists.
    assert await _fetch_parts(db_pool, f["nid"]) is not None
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            # No parts fields submitted.
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert await _fetch_parts(db_pool, f["nid"]) is None


async def test_edit_row_post_no_parts_fields_when_no_existing_row_is_no_op(
    client, person_with_legal_name, db_pool
):
    """Issue #127: when the row never had parts and none submitted, no row written."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={"name": "X", "name_type": "legal", "is_canonical": "true"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert await _fetch_parts(db_pool, f["nid"]) is None


# ---- caps + validation ------------------------------------------------------


async def test_edit_row_post_parts_cap_violation_flashes_and_skips_name_update(
    client, person_with_legal_name, db_pool
):
    """Issue #127: parts validation rolls back the whole transaction.

    A 6-element given_names submission must NOT change the name."""
    f = person_with_legal_name
    original_name = f["name"]
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": "Changed Name",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": [f"name{i}" for i in range(6)],
        },
        headers=HTMX_HEADERS,
    )
    # HTMX path: 200 + flash trigger; non-HTMX would be 422.
    assert r.status_code == 200, r.text
    assert "HX-Trigger" in r.headers
    assert "5" in r.headers["HX-Trigger"] or "five" in r.headers["HX-Trigger"].lower()
    # Verify name was NOT updated (transaction rolled back).
    name_row = await _fetch_name_row(db_pool, f["nid"])
    assert name_row["name"] == original_name
    assert await _fetch_parts(db_pool, f["nid"]) is None  # no row written either


async def test_edit_row_post_non_htmx_cap_returns_422(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "family_names": [f"name{i}" for i in range(6)],
        },
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422, r.text


async def test_edit_row_post_rejects_unknown_primary_identifier(
    client, person_with_legal_name, db_pool
):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Ada"],
            "primary_identifier": "nonsense",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "HX-Trigger" in r.headers
    assert await _fetch_parts(db_pool, f["nid"]) is None
    # Name is also unchanged because the transaction rolled back.
    assert (await _fetch_name_row(db_pool, f["nid"]))["name"] == f["name"]


async def test_edit_row_post_accepts_blank_primary_identifier(
    client, person_with_legal_name, db_pool
):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Ada"],
            "primary_identifier": "",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = await _fetch_parts(db_pool, f["nid"])
    assert parts["primary_identifier"] is None
    assert parts["given_names"] == ["Ada"]


# ---- create flow — POST / accepts parts payload ----------------------------


async def test_create_name_with_parts_payload_inserts_both(client, person_only, db_pool):
    """Issue #127: POST / (create) accepts parts fields and upserts both rows."""
    pid = person_only["pid"]
    r = client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Ada Lovelace",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Ada"],
            "family_names": ["Lovelace"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    new_name = await _fetch_canonical_name(db_pool, pid)
    assert new_name is not None
    assert new_name["name"] == "Ada Lovelace"
    parts = await _fetch_parts(db_pool, new_name["id"])
    assert parts is not None
    assert parts["given_names"] == ["Ada"]
    assert parts["family_names"] == ["Lovelace"]
    assert parts["primary_identifier"] == "family"


async def test_create_name_without_parts_payload_skips_parts_helper(client, person_only, db_pool):
    """Issue #127: create without parts fields short-circuits before the
    parts helper. The just-inserted name has no parts row to upsert or
    delete, so `name_create` skips `upsert_or_delete_parts` entirely
    (avoiding a zero-row DELETE round-trip)."""
    pid = person_only["pid"]
    r = client.post(
        f"/admin/people/{pid}/names/",
        data={"name": "Plain Name", "name_type": "legal", "is_canonical": "true"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    new_name = await _fetch_canonical_name(db_pool, pid)
    assert new_name is not None
    assert await _fetch_parts(db_pool, new_name["id"]) is None


async def test_create_name_parts_cap_violation_rolls_back_name_insert(client, person_only, db_pool):
    """Issue #127: cap-violation on create rolls back the whole transaction —
    no name row is inserted."""
    pid = person_only["pid"]
    r = client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Should Not Persist",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": [f"name{i}" for i in range(6)],
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "HX-Trigger" in r.headers
    # No canonical name written — transaction rolled back.
    assert await _fetch_canonical_name(db_pool, pid) is None


async def test_create_name_non_htmx_cap_returns_422(client, person_only, db_pool):
    """Issue #127: parts cap-violation on the create path surfaces as 422
    for non-HTMX clients (mirrors `test_edit_row_post_non_htmx_cap_returns_422`)."""
    pid = person_only["pid"]
    r = client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Should Not Persist",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": [f"name{i}" for i in range(6)],
        },
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422, r.text
    # Transaction still rolled back even on the non-HTMX 422 path.
    assert await _fetch_canonical_name(db_pool, pid) is None


# ---- pre-population + cascade -----------------------------------------------


async def test_edit_form_pre_populates_parts(client, person_with_parts):
    """Opening the edit row for a name with parts pre-fills the editor."""
    f = person_with_parts
    r = client.get(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert 'value="Ada"' in r.text
    assert 'value="Lovelace"' in r.text
    # primary_identifier=family selected
    assert ('value="family" selected' in r.text) or ("selected>family" in r.text)


async def test_detail_page_shows_parts_summary_after_save(client, person_with_legal_name):
    """Round-trip: POST combined name+parts → reload detail → subtitle present."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        data={
            "name": f["name"],
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["María", "José"],
            "family_names": ["García", "López"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    r = client.get(
        f"/admin/people/{f['pid']}/",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "García López" in r.text
    assert "María José" in r.text


async def test_parts_cascade_when_parent_name_deleted(client, person_with_parts, db_pool):
    """Deleting the parent person_names row cascades to person_name_parts."""
    f = person_with_parts
    assert await _fetch_parts(db_pool, f["nid"]) is not None

    # Add a second name so the cascade-delete passes the last-identity guard.
    second_nid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, visibility)"
            " VALUES ($1, $2, 'Backup', 'preferred', FALSE, 'public')",
            second_nid,
            f["pid"],
        )

    r = client.delete(
        f"/admin/people/{f['pid']}/names/{f['nid']}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert await _fetch_parts(db_pool, f["nid"]) is None
