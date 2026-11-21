"""The names panel shows which API key wrote each name (#847).

A name with a `source_key_id` shows a "via ‹key label›" badge; a NULL stamp
(admin, importer, applier, or a deleted key) shows nothing. Every render path
of a name row carries it — the detail page's first render, the tbody re-render
after a mutation, and the single-row Cancel read-row — for people and, through
the shared names factory, for orgs. A person row's badge links to the
key-written-names review page filtered to that key.
"""

import hashlib
import os
import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
_LABEL = "Observo 847 <test>"
_LABEL_ESCAPED = "Observo 847 &lt;test&gt;"


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
async def key_id(db):
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@847.test")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        _LABEL,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return kid


@pytest_asyncio.fixture(loop_scope="session")
async def person(db, key_id):
    """A person with a canonical admin name, a key-written variant, and a spare."""
    pid = generate_id()
    ids = {"pid": pid, "canonical": generate_id(), "stamped": generate_id(), "spare": generate_id()}
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    for nid, name, ntype, canonical, key in (
        (ids["canonical"], "Canon Person 847", "legal", True, None),
        (ids["stamped"], "Keyed Variant 847", "variant", False, key_id),
        (ids["spare"], "Spare Alias 847", "alias", False, None),
    ):
        await db.execute(
            "INSERT INTO person_names"
            " (id, person_id, name, name_type, is_canonical, source_key_id)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            nid,
            pid,
            name,
            ntype,
            canonical,
            key,
        )
    return ids


@pytest_asyncio.fixture(loop_scope="session")
async def org(db, key_id):
    oid = generate_id()
    ids = {"oid": oid, "canonical": generate_id(), "stamped": generate_id(), "spare": generate_id()}
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    for nid, name, canonical, key in (
        (ids["canonical"], "Canon Org 847", True, None),
        (ids["stamped"], "Keyed Org Name 847", False, key_id),
        (ids["spare"], "Spare Org Name 847", False, None),
    ):
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical, source_key_id)"
            " VALUES ($1, $2, $3, $4, $5)",
            nid,
            oid,
            name,
            canonical,
            key,
        )
    return ids


def _row(html: str, name_id: str) -> str:
    m = re.search(rf'<tr id="name-row-{re.escape(name_id)}".*?</tr>', html, re.S)
    assert m, f"no name row for {name_id}"
    return m.group(0)


def _assert_badge(row: str, key_id: str | None, *, linked: bool) -> None:
    assert "data-source-key" in row
    assert _LABEL_ESCAPED in row
    assert _LABEL not in row
    link = f'href="/admin/activity/names/?key={key_id}"'
    assert (link in row) is linked


def _assert_no_badge(row: str) -> None:
    assert "data-source-key" not in row


# ---- People --------------------------------------------------------------------


async def test_person_detail_badges_the_key_written_name_only(client, person, key_id):
    r = await client.get(f"/admin/people/{person['pid']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    _assert_badge(_row(r.text, person["stamped"]), key_id, linked=True)
    _assert_no_badge(_row(r.text, person["canonical"]))
    _assert_no_badge(_row(r.text, person["spare"]))


async def test_person_tbody_rerender_keeps_the_badge(client, person, key_id):
    r = await client.delete(
        f"/admin/people/{person['pid']}/names/{person['spare']}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    _assert_badge(_row(r.text, person["stamped"]), key_id, linked=True)
    _assert_no_badge(_row(r.text, person["canonical"]))


async def test_person_read_row_keeps_the_badge(client, person, key_id):
    base = f"/admin/people/{person['pid']}/names"
    r = await client.get(f"{base}/{person['stamped']}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    _assert_badge(_row(r.text, person["stamped"]), key_id, linked=True)
    r = await client.get(f"{base}/{person['spare']}/read-row/", headers=HTMX_HEADERS)
    _assert_no_badge(_row(r.text, person["spare"]))


async def test_deleted_key_leaves_no_badge(client, db, person, key_id):
    """`ON DELETE SET NULL`: a deleted key's names lose their stamp, and the badge."""
    await db.execute("DELETE FROM api_keys WHERE id=$1", key_id)
    r = await client.get(f"/admin/people/{person['pid']}/", headers=AUTH_HEADERS)
    _assert_no_badge(_row(r.text, person["stamped"]))


# ---- Orgs (through the shared names factory) ------------------------------------


async def test_org_detail_badges_the_key_written_name_only(client, org, key_id):
    r = await client.get(f"/admin/orgs/{org['oid']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    _assert_badge(_row(r.text, org["stamped"]), key_id, linked=False)
    _assert_no_badge(_row(r.text, org["canonical"]))


async def test_org_tbody_rerender_keeps_the_badge(client, org, key_id):
    r = await client.delete(f"/admin/orgs/{org['oid']}/names/{org['spare']}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    _assert_badge(_row(r.text, org["stamped"]), key_id, linked=False)


async def test_org_read_row_keeps_the_badge(client, org, key_id):
    r = await client.get(
        f"/admin/orgs/{org['oid']}/names/{org['stamped']}/read-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    _assert_badge(_row(r.text, org["stamped"]), key_id, linked=False)
