"""Phase 2d Task 1 — `person_name_parts` CRUD endpoints.

Routes (mounted under /admin/people/{person_id}/names/{name_id}/parts):
  POST `/`           — upsert (INSERT … ON CONFLICT DO UPDATE).
  POST `/delete/`    — DELETE the parts row.

Form encoding: arrays repeat (`given_names=María&given_names=José`).
Caps: 5 elements per array; empty strings filtered before INSERT.
`primary_identifier` allowlist: family / given / patronymic / mononym / blank.
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
def person_with_legal_name():
    """One person + one canonical legal name (no parts row)."""
    dsn = _dsn()
    pid = generate_id()
    nid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'María José García López', 'legal', TRUE, 'public')",
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
    yield {"pid": pid, "nid": nid}
    asyncio.run(teardown())


async def _fetch_parts(name_id: str) -> dict | None:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT given_names, family_names, additional_names,"
            " honorific_prefix, honorific_suffix, primary_identifier"
            " FROM person_name_parts WHERE person_name_id=$1",
            name_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


# ---- upsert: insert path ----------------------------------------------------


def test_upsert_inserts_when_no_row_exists(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={
            "given_names": ["María", "José"],
            "family_names": ["García", "López"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts is not None
    assert parts["given_names"] == ["María", "José"]
    assert parts["family_names"] == ["García", "López"]
    assert parts["primary_identifier"] == "family"


def test_upsert_persists_honorifics(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={
            "given_names": ["Ada"],
            "honorific_prefix": "Dr.",
            "honorific_suffix": "FRS",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts["honorific_prefix"] == "Dr."
    assert parts["honorific_suffix"] == "FRS"


def test_upsert_filters_empty_string_array_entries(client, person_with_legal_name):
    """Empty strings in repeating fields are dropped before INSERT."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["María", "", "  ", "José"]},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts["given_names"] == ["María", "José"]


def test_upsert_with_all_empty_does_not_create_row(client, person_with_legal_name):
    """If every field is empty/blank, no row is inserted."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": [""], "family_names": [""], "primary_identifier": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts is None


# ---- upsert: update path ----------------------------------------------------


def test_upsert_updates_when_row_exists(client, person_with_legal_name):
    """Second POST overwrites — no UniqueViolation, full replacement of arrays."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Maria"], "primary_identifier": "given"},
        headers=HTMX_HEADERS,
    )
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={
            "given_names": ["María", "José"],
            "family_names": ["García"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts["given_names"] == ["María", "José"]
    assert parts["family_names"] == ["García"]
    assert parts["primary_identifier"] == "family"


def test_upsert_clears_arrays_when_omitted(client, person_with_legal_name):
    """Subsequent POST without given_names drops the previously-saved values."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Maria"], "family_names": ["Smith"]},
        headers=HTMX_HEADERS,
    )
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"family_names": ["Smith", "Jones"]},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts["given_names"] is None or parts["given_names"] == []
    assert parts["family_names"] == ["Smith", "Jones"]


# ---- caps + validation ------------------------------------------------------


def test_upsert_rejects_more_than_five_given_names(
    client, person_with_legal_name,
):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": [f"name{i}" for i in range(6)]},
        headers=HTMX_HEADERS,
    )
    # HTMX path: 200 + flash trigger; non-HTMX would be 422.
    assert r.status_code == 200, r.text
    assert "HX-Trigger" in r.headers
    assert "5" in r.headers["HX-Trigger"] or "five" in r.headers["HX-Trigger"].lower()
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts is None  # no row written on rejection


def test_upsert_non_htmx_cap_returns_422(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"family_names": [f"name{i}" for i in range(6)]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422, r.text


def test_upsert_rejects_unknown_primary_identifier(
    client, person_with_legal_name,
):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"], "primary_identifier": "nonsense"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "HX-Trigger" in r.headers
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts is None


def test_upsert_accepts_blank_primary_identifier(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"], "primary_identifier": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    parts = asyncio.run(_fetch_parts(f["nid"]))
    assert parts["primary_identifier"] is None
    assert parts["given_names"] == ["Ada"]


# ---- delete -----------------------------------------------------------------


def test_delete_removes_parts_row(client, person_with_legal_name):
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"], "primary_identifier": "given"},
        headers=HTMX_HEADERS,
    )
    assert asyncio.run(_fetch_parts(f["nid"])) is not None
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/delete/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert asyncio.run(_fetch_parts(f["nid"])) is None


def test_delete_when_no_row_is_idempotent(client, person_with_legal_name):
    """Deleting nonexistent parts is a no-op, not a 404."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/delete/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text


# ---- guards -----------------------------------------------------------------


def test_upsert_404_for_unknown_name(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/nid_does_not_exist/parts/",
        data={"given_names": ["Ada"]},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


def test_upsert_404_for_cross_person_name(client, person_with_legal_name):
    """A name id belonging to another person → 404, not write."""
    f = person_with_legal_name
    other_pid = generate_id()
    other_nid = generate_id()

    async def setup_other():
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", other_pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Other', 'legal', TRUE, 'public')",
                other_nid, other_pid,
            )
        finally:
            await conn.close()

    async def teardown_other():
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "DELETE FROM person_names WHERE person_id=$1", other_pid,
            )
            await conn.execute("DELETE FROM people WHERE id=$1", other_pid)
        finally:
            await conn.close()

    asyncio.run(setup_other())
    try:
        r = client.post(
            f"/admin/people/{f['pid']}/names/{other_nid}/parts/",
            data={"given_names": ["Ada"]},
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 404
    finally:
        asyncio.run(teardown_other())


def test_upsert_requires_admin_auth(client, person_with_legal_name):
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"]},
        # No headers — should redirect.
        follow_redirects=False,
    )
    assert r.status_code == 307


# ---- cascade ----------------------------------------------------------------


def test_detail_page_shows_parts_summary_after_save(
    client, person_with_legal_name,
):
    """Round-trip: POST parts → reload detail → subtitle present."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={
            "given_names": ["María", "José"],
            "family_names": ["García", "López"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    r = client.get(
        f"/admin/people/{f['pid']}/", headers=AUTH_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "parts:" in r.text
    assert "García López" in r.text
    assert "María José" in r.text


def test_edit_form_pre_populates_parts(client, person_with_legal_name):
    """Opening the edit row for a name with parts pre-fills the editor."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={
            "given_names": ["Ada"],
            "family_names": ["Lovelace"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    r = client.get(
        f"/admin/people/{f['pid']}/names/{f['nid']}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert 'value="Ada"' in r.text
    assert 'value="Lovelace"' in r.text
    # primary_identifier=family selected
    assert ('value="family" selected' in r.text) or ('selected>family' in r.text)


def test_upsert_response_includes_oob_summary_with_set_badge(
    client, person_with_legal_name,
):
    """After save, the response must contain an OOB summary fragment
    that updates the editor's badge to 'set' without collapsing the
    user's open <details>."""
    f = person_with_legal_name
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"], "primary_identifier": "given"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert f'id="parts-summary-{f["nid"]}"' in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text
    assert "Structured parts" in r.text
    assert "badge--inactive" in r.text  # the "set" badge


def test_delete_response_includes_oob_summary_without_set_badge(
    client, person_with_legal_name,
):
    """Delete must remove the 'set' badge via the same OOB pattern."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"]},
        headers=HTMX_HEADERS,
    )
    r = client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/delete/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert f'id="parts-summary-{f["nid"]}"' in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text
    assert "Structured parts" in r.text
    assert "badge--inactive" not in r.text  # badge gone


# ---- cascade ----------------------------------------------------------------


def test_parts_cascade_when_parent_name_deleted(client, person_with_legal_name):
    """Deleting the parent person_names row cascades to person_name_parts."""
    f = person_with_legal_name
    client.post(
        f"/admin/people/{f['pid']}/names/{f['nid']}/parts/",
        data={"given_names": ["Ada"], "primary_identifier": "given"},
        headers=HTMX_HEADERS,
    )
    assert asyncio.run(_fetch_parts(f["nid"])) is not None

    # Add a second name first so the cascade-delete passes the last-identity guard.
    second_nid = generate_id()
    async def add_second():
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical, visibility)"
                " VALUES ($1, $2, 'Backup', 'preferred', FALSE, 'public')",
                second_nid, f["pid"],
            )
        finally:
            await conn.close()
    asyncio.run(add_second())

    r = client.delete(
        f"/admin/people/{f['pid']}/names/{f['nid']}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert asyncio.run(_fetch_parts(f["nid"])) is None
