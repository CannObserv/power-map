"""Integration tests for jurisdiction graph editing — relationships (#275 Phase 3)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HX = {**AUTH_HEADERS, "HX-Request": "true"}


async def jurisdiction_change_count(db, jurisdiction_id):
    """Count change-feed rows for a jurisdiction on the test connection.

    Local variant of the shared conftest helper: reads the same open,
    uncommitted transaction the routes wrote to (the shared helper acquires a
    separate pool connection and would not see the rollback-scoped writes).
    """
    return await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_type='jurisdiction' AND entity_id=$1",
        jurisdiction_id,
    )


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
async def county_type_id(db):
    return await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")


@pytest_asyncio.fixture(loop_scope="session")
async def two_jurs(db, county_type_id):
    """Source + target county jurisdictions; rolled back with the test transaction."""
    a, b = generate_id(), generate_id()
    for jid, name in [(a, "Alpha County"), (b, "Beta County")]:
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            f"rel-{jid[-8:].lower()}",
            name,
            county_type_id,
        )
    return {"a": a, "b": b}


@pytest_asyncio.fixture(loop_scope="session")
async def rel_types(db):
    sym = await db.fetchval("SELECT id FROM jurisdiction_relationship_types WHERE slug='borders'")
    asym = await db.fetchval("SELECT id FROM jurisdiction_relationship_types WHERE slug='governs'")
    return {"symmetric": sym, "asymmetric": asym}


async def test_relationship_new_row_form(client, two_jurs):
    r = await client.get(f"/admin/jurisdictions/{two_jurs['a']}/relationships/new-row/", headers=HX)
    assert r.status_code == 200
    assert 'name="target_id"' in r.text  # hidden id set by the typeahead
    assert 'name="rel_type_id"' in r.text
    assert "/admin/jurisdictions/search/" in r.text  # target typeahead reused


async def test_relationship_add_symmetric(client, two_jurs, rel_types, db):
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["symmetric"],
            "direction": "outgoing",
            "valid_from": "",
            "valid_until": "",
            "notes": "",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT from_id, to_id FROM jurisdiction_relationships"
        " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
        rel_types["symmetric"],
        two_jurs["a"],
    )
    assert row is not None
    assert {row["from_id"], row["to_id"]} == {two_jurs["a"], two_jurs["b"]}


async def test_relationship_add_asymmetric_outgoing(client, two_jurs, rel_types, db):
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "outgoing",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT from_id, to_id FROM jurisdiction_relationships"
        " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
        rel_types["asymmetric"],
        two_jurs["a"],
    )
    # outgoing → current is the FROM side
    assert row["from_id"] == two_jurs["a"]
    assert row["to_id"] == two_jurs["b"]


async def test_relationship_add_asymmetric_incoming(client, two_jurs, rel_types, db):
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "incoming",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT from_id, to_id FROM jurisdiction_relationships"
        " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
        rel_types["asymmetric"],
        two_jurs["a"],
    )
    # incoming → current is the TO side (target is FROM)
    assert row["from_id"] == two_jurs["b"]
    assert row["to_id"] == two_jurs["a"]


async def test_relationship_add_self_rejected(client, two_jurs, rel_types):
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["a"],
            "rel_type_id": rel_types["symmetric"],
            "direction": "outgoing",
        },
    )
    assert r.status_code == 422


async def test_relationship_add_invalid_range(client, two_jurs, rel_types):
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "outgoing",
            "valid_from": "2020-01-01",
            "valid_until": "2010-01-01",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Inline validity edit + delete
# ---------------------------------------------------------------------------


async def _make_edge(db, from_id, to_id, rel_type_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        rid,
        from_id,
        to_id,
        rel_type_id,
    )
    return rid


async def test_relationship_edit_row_form(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = await client.get(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/", headers=HX
    )
    assert r.status_code == 200
    assert 'name="valid_from"' in r.text
    assert 'name="valid_until"' in r.text


async def test_relationship_edit_validity(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/",
        headers=HX,
        data={"valid_from": "2015-01-01", "valid_until": "2020-12-31", "notes": "ended"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT valid_from, valid_until, notes FROM jurisdiction_relationships WHERE id=$1",
        rid,
    )
    assert str(row["valid_from"]) == "2015-01-01"
    assert str(row["valid_until"]) == "2020-12-31"
    assert row["notes"] == "ended"


async def test_relationship_edit_invalid_range(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/",
        headers=HX,
        data={"valid_from": "2020-01-01", "valid_until": "2010-01-01"},
    )
    assert r.status_code == 422


async def test_relationship_delete(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["symmetric"])
    r = await client.request(
        "DELETE", f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/", headers=HX
    )
    assert r.status_code == 200
    assert await db.fetchval("SELECT id FROM jurisdiction_relationships WHERE id=$1", rid) is None


async def test_relationship_delete_not_found(client, two_jurs):
    r = await client.request(
        "DELETE",
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{generate_id()}/",
        headers=HX,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Change-feed propagation (touch_parent_jurisdiction trigger)
# ---------------------------------------------------------------------------


async def test_relationship_add_emits_change_feed(client, two_jurs, rel_types, db):
    before_a = await jurisdiction_change_count(db, two_jurs["a"])
    before_b = await jurisdiction_change_count(db, two_jurs["b"])
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "outgoing",
        },
    )
    assert r.status_code == 200
    # both endpoints of the edge are touched → each gets a fresh 'updated' change
    assert await jurisdiction_change_count(db, two_jurs["a"]) > before_a
    assert await jurisdiction_change_count(db, two_jurs["b"]) > before_b


async def test_relationship_delete_emits_change_feed(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["symmetric"])
    before_a = await jurisdiction_change_count(db, two_jurs["a"])
    before_b = await jurisdiction_change_count(db, two_jurs["b"])
    r = await client.request(
        "DELETE", f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/", headers=HX
    )
    assert r.status_code == 200
    assert await jurisdiction_change_count(db, two_jurs["a"]) > before_a
    assert await jurisdiction_change_count(db, two_jurs["b"]) > before_b


async def test_relationship_edit_emits_change_feed(client, two_jurs, rel_types, db):
    rid = await _make_edge(db, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    before_a = await jurisdiction_change_count(db, two_jurs["a"])
    before_b = await jurisdiction_change_count(db, two_jurs["b"])
    r = await client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/",
        headers=HX,
        data={"valid_from": "2001-01-01", "valid_until": "", "notes": "ended"},
    )
    assert r.status_code == 200
    # the validity UPDATE touches both endpoints via the trigger
    assert await jurisdiction_change_count(db, two_jurs["a"]) > before_a
    assert await jurisdiction_change_count(db, two_jurs["b"]) > before_b
