"""Integration tests for jurisdiction graph editing — relationships (#275 Phase 3)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HX = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def county_type_id(db_pool):
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")


@pytest_asyncio.fixture(loop_scope="session")
async def two_jurs(db_pool, county_type_id):
    """Source + target county jurisdictions; edges + rows cleaned at teardown."""
    a, b = generate_id(), generate_id()
    async with db_pool.acquire() as conn:
        for jid, name in [(a, "Alpha County"), (b, "Beta County")]:
            await conn.execute(
                "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
                jid,
                f"rel-{jid[-8:].lower()}",
                name,
                county_type_id,
            )
    yield {"a": a, "b": b}
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM jurisdiction_relationships"
            " WHERE from_id = ANY($1::text[]) OR to_id = ANY($1::text[])",
            [a, b],
        )
        await conn.execute("DELETE FROM jurisdictions WHERE id = ANY($1::text[])", [a, b])


@pytest_asyncio.fixture(loop_scope="session")
async def rel_types(db_pool):
    async with db_pool.acquire() as conn:
        sym = await conn.fetchval(
            "SELECT id FROM jurisdiction_relationship_types WHERE slug='borders'"
        )
        asym = await conn.fetchval(
            "SELECT id FROM jurisdiction_relationship_types WHERE slug='governs'"
        )
    return {"symmetric": sym, "asymmetric": asym}


async def test_relationship_new_row_form(client, two_jurs):
    r = client.get(f"/admin/jurisdictions/{two_jurs['a']}/relationships/new-row/", headers=HX)
    assert r.status_code == 200
    assert 'name="target_id"' in r.text  # hidden id set by the typeahead
    assert 'name="rel_type_id"' in r.text
    assert "/admin/jurisdictions/search/" in r.text  # target typeahead reused


async def test_relationship_add_symmetric(client, two_jurs, rel_types, db_pool):
    r = client.post(
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
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT from_id, to_id FROM jurisdiction_relationships"
            " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
            rel_types["symmetric"],
            two_jurs["a"],
        )
    assert row is not None
    assert {row["from_id"], row["to_id"]} == {two_jurs["a"], two_jurs["b"]}


async def test_relationship_add_asymmetric_outgoing(client, two_jurs, rel_types, db_pool):
    r = client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "outgoing",
        },
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT from_id, to_id FROM jurisdiction_relationships"
            " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
            rel_types["asymmetric"],
            two_jurs["a"],
        )
    # outgoing → current is the FROM side
    assert row["from_id"] == two_jurs["a"]
    assert row["to_id"] == two_jurs["b"]


async def test_relationship_add_asymmetric_incoming(client, two_jurs, rel_types, db_pool):
    r = client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/",
        headers=HX,
        data={
            "target_id": two_jurs["b"],
            "rel_type_id": rel_types["asymmetric"],
            "direction": "incoming",
        },
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT from_id, to_id FROM jurisdiction_relationships"
            " WHERE rel_type_id=$1 AND (from_id=$2 OR to_id=$2)",
            rel_types["asymmetric"],
            two_jurs["a"],
        )
    # incoming → current is the TO side (target is FROM)
    assert row["from_id"] == two_jurs["b"]
    assert row["to_id"] == two_jurs["a"]


async def test_relationship_add_self_rejected(client, two_jurs, rel_types):
    r = client.post(
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
    r = client.post(
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


async def _make_edge(db_pool, from_id, to_id, rel_type_id):
    rid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
            " VALUES ($1,$2,$3,$4)",
            rid,
            from_id,
            to_id,
            rel_type_id,
        )
    return rid


async def test_relationship_edit_row_form(client, two_jurs, rel_types, db_pool):
    rid = await _make_edge(db_pool, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = client.get(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/", headers=HX
    )
    assert r.status_code == 200
    assert 'name="valid_from"' in r.text
    assert 'name="valid_until"' in r.text


async def test_relationship_edit_validity(client, two_jurs, rel_types, db_pool):
    rid = await _make_edge(db_pool, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/",
        headers=HX,
        data={"valid_from": "2015-01-01", "valid_until": "2020-12-31", "notes": "ended"},
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT valid_from, valid_until, notes FROM jurisdiction_relationships WHERE id=$1",
            rid,
        )
    assert str(row["valid_from"]) == "2015-01-01"
    assert str(row["valid_until"]) == "2020-12-31"
    assert row["notes"] == "ended"


async def test_relationship_edit_invalid_range(client, two_jurs, rel_types, db_pool):
    rid = await _make_edge(db_pool, two_jurs["a"], two_jurs["b"], rel_types["asymmetric"])
    r = client.post(
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/edit-row/",
        headers=HX,
        data={"valid_from": "2020-01-01", "valid_until": "2010-01-01"},
    )
    assert r.status_code == 422


async def test_relationship_delete(client, two_jurs, rel_types, db_pool):
    rid = await _make_edge(db_pool, two_jurs["a"], two_jurs["b"], rel_types["symmetric"])
    r = client.request(
        "DELETE", f"/admin/jurisdictions/{two_jurs['a']}/relationships/{rid}/", headers=HX
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT id FROM jurisdiction_relationships WHERE id=$1", rid)
            is None
        )


async def test_relationship_delete_not_found(client, two_jurs):
    r = client.request(
        "DELETE",
        f"/admin/jurisdictions/{two_jurs['a']}/relationships/{generate_id()}/",
        headers=HX,
    )
    assert r.status_code == 404
