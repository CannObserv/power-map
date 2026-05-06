"""Phase 2a backend tests — person-name visibility metadata.

Covers the `supports_person_metadata=True` flag on `make_names_router`:
- visibility round-trip (create + edit accept the Form field, persist to DB)
- expanded `name_type` values accepted (all 12 from CONVENTIONS.md)
- deadname coercion via DB trigger (public → legal_only on insert/update)
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


def _dsn():
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


async def _fetch_visibility(pid: str, nid: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT visibility FROM person_names WHERE id=$1 AND person_id=$2",
            nid, pid,
        )
    finally:
        await conn.close()


async def _fetch_name_type(pid: str, nid: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT name_type FROM person_names WHERE id=$1 AND person_id=$2",
            nid, pid,
        )
    finally:
        await conn.close()


# ---- visibility round-trip on create -------------------------------------


def test_create_persists_visibility_legal_only(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
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
    nid = asyncio.run(_fetch_new_name_id(pid, "Legal Only Name"))
    assert asyncio.run(_fetch_visibility(pid, nid)) == "legal_only"


def test_create_persists_visibility_hidden(client, person_and_name):
    pid, _ = person_and_name
    r = client.post(
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
    nid = asyncio.run(_fetch_new_name_id(pid, "Hidden Name"))
    assert asyncio.run(_fetch_visibility(pid, nid)) == "hidden"


def test_create_defaults_visibility_to_public(client, person_and_name):
    """Visibility omitted → DB default 'public' applies."""
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": "Public Default",
            "name_type": "preferred",
            "is_canonical": "",
        },
    )
    assert r.status_code == 200
    nid = asyncio.run(_fetch_new_name_id(pid, "Public Default"))
    assert asyncio.run(_fetch_visibility(pid, nid)) == "public"


# ---- visibility round-trip on edit ---------------------------------------


def test_edit_persists_visibility(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
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
    assert asyncio.run(_fetch_visibility(pid, nid)) == "legal_only"


# ---- invalid visibility rejected with 422 --------------------------------


def test_create_rejects_invalid_visibility(client, person_and_name):
    """Out-of-range visibility values return 422 (Pydantic Literal validation)."""
    pid, _ = person_and_name
    r = client.post(
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


def test_edit_rejects_invalid_visibility(client, person_and_name):
    pid, nid = person_and_name
    r = client.post(
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


# ---- expanded name_type values -------------------------------------------


@pytest.mark.parametrize(
    "name_type",
    [
        "legal", "preferred", "alias", "former", "initials",
        "maiden", "religious", "stage", "deadname",
        "reading", "romanization", "mrz",
    ],
)
def test_create_accepts_all_name_types(client, person_and_name, name_type):
    pid, _ = person_and_name
    r = client.post(
        f"/admin/people/{pid}/names/",
        headers=HTMX_HEADERS,
        data={
            "name": f"Test {name_type}",
            "name_type": name_type,
            "is_canonical": "",
        },
    )
    assert r.status_code == 200, r.text
    nid = asyncio.run(_fetch_new_name_id(pid, f"Test {name_type}"))
    assert asyncio.run(_fetch_name_type(pid, nid)) == name_type


# ---- deadname → legal_only coercion via trg_deadname_visibility ----------


def test_create_deadname_coerces_public_to_legal_only(client, person_and_name):
    """Public visibility on deadname row → DB trigger downgrades to legal_only."""
    pid, _ = person_and_name
    r = client.post(
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
    nid = asyncio.run(_fetch_new_name_id(pid, "Pre-Transition Name"))
    assert asyncio.run(_fetch_visibility(pid, nid)) == "legal_only"


def test_create_deadname_preserves_explicit_hidden(client, person_and_name):
    """Explicit 'hidden' on deadname row is preserved (trigger only downgrades public)."""
    pid, _ = person_and_name
    r = client.post(
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
    nid = asyncio.run(_fetch_new_name_id(pid, "Hidden Deadname"))
    assert asyncio.run(_fetch_visibility(pid, nid)) == "hidden"


# ---- helper --------------------------------------------------------------


async def _fetch_new_name_id(pid: str, name: str) -> str:
    conn = await asyncpg.connect(_dsn())
    try:
        return await conn.fetchval(
            "SELECT id FROM person_names WHERE person_id=$1 AND name=$2",
            pid, name,
        )
    finally:
        await conn.close()
