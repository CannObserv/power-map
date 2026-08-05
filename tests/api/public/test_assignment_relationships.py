"""Endpoint tests for the public assignment-relationship API (#301).

POST /api/v1/assignment-relationships/observations (partial-success)
GET  /api/v1/assignments/{pm_assignment_id}/relationships
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


async def _key_with_scopes(db, scopes: list[str]) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{kid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "rel key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    for s in scopes:
        await db.execute("INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, s)
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db) -> str:
    return await _key_with_scopes(db, ["assignment_relationships:write"])


@pytest_asyncio.fixture(loop_scope="session")
async def read_key(db) -> str:
    return await _key_with_scopes(db, ["assignment_relationships:read"])


async def _assignment(db) -> str:
    org, person, role, aid = generate_id(), generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", role, org, "R"
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)", aid, person, role
    )
    return aid


OBS = "/api/v1/assignment-relationships/observations"


async def test_observe_new_and_read_back_both_directions(client, db, write_key, read_key):
    frm, to = await _assignment(db), await _assignment(db)
    r = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={
            "relationships": [
                {
                    "from_pm_assignment_id": frm,
                    "to_pm_assignment_id": to,
                    "valid_from": "2023-01-01",
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["disposition"] == "new"
    rel_id = result["relationship_id"]

    # readable from the staffer side
    g = await client.get(
        f"/api/v1/assignments/{frm}/relationships", headers={"X-API-Key": read_key}
    )
    assert g.status_code == 200
    body = g.json()
    assert body["meta"]["count"] == 1
    row = body["data"][0]
    assert row["id"] == rel_id
    assert row["rel_type"] == "staff_of"
    assert row["from_assignment_id"] == frm
    assert row["to_assignment_id"] == to
    assert row["valid_from"] == "2023-01-01"

    # and from the principal side
    g2 = await client.get(
        f"/api/v1/assignments/{to}/relationships", headers={"X-API-Key": read_key}
    )
    assert g2.json()["meta"]["count"] == 1


async def test_partial_success(client, db, write_key):
    frm, to = await _assignment(db), await _assignment(db)
    r = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={
            "relationships": [
                {"from_pm_assignment_id": frm, "to_pm_assignment_id": to},
                {"from_pm_assignment_id": frm, "to_pm_assignment_id": frm},  # self → rejected
                {"from_pm_assignment_id": frm, "to_pm_assignment_id": generate_id()},  # unresolved
            ]
        },
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "new"
    assert results[1]["disposition"] == "rejected"
    assert results[1]["reason"] == "self_relationship"
    assert results[2]["reason"] == "assignment_unresolved"


async def test_refine_and_retract(client, db, write_key, read_key):
    frm, to = await _assignment(db), await _assignment(db)
    r = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={"relationships": [{"from_pm_assignment_id": frm, "to_pm_assignment_id": to}]},
    )
    rel_id = r.json()["results"][0]["relationship_id"]

    # refine via pm_relationship_id
    r2 = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={"relationships": [{"pm_relationship_id": rel_id, "notes": "chief aide"}]},
    )
    assert r2.json()["results"][0]["disposition"] == "updated"

    # retract
    r3 = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={"relationships": [{"pm_relationship_id": rel_id, "op": "retract"}]},
    )
    assert r3.json()["results"][0]["disposition"] == "retracted"

    # gone from the default (active) read, present with include_archived
    g = await client.get(
        f"/api/v1/assignments/{frm}/relationships", headers={"X-API-Key": read_key}
    )
    assert g.json()["meta"]["count"] == 0
    g2 = await client.get(
        f"/api/v1/assignments/{frm}/relationships?include_archived=true",
        headers={"X-API-Key": read_key},
    )
    assert g2.json()["meta"]["count"] == 1


async def test_write_requires_scope(client, db, read_key):
    frm, to = await _assignment(db), await _assignment(db)
    r = await client.post(
        OBS,
        headers={"X-API-Key": read_key},  # read scope only
        json={"relationships": [{"from_pm_assignment_id": frm, "to_pm_assignment_id": to}]},
    )
    assert r.status_code == 403


async def test_read_requires_scope(client, db, write_key):
    frm = await _assignment(db)
    r = await client.get(
        f"/api/v1/assignments/{frm}/relationships", headers={"X-API-Key": write_key}
    )
    assert r.status_code == 403


async def test_invalid_key_401(client, db):
    frm = await _assignment(db)
    r = await client.get(
        f"/api/v1/assignments/{frm}/relationships", headers={"X-API-Key": "pm_bogus"}
    )
    assert r.status_code == 401


# ── conditional GET (#392) ────────────────────────────────────────────────────


def _rels(aid: str) -> str:
    return f"/api/v1/assignments/{aid}/relationships"


async def _edge(client, write_key, frm: str, to: str) -> str:
    r = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={
            "relationships": [
                {"from_pm_assignment_id": frm, "to_pm_assignment_id": to, "rel_type": "staff_of"}
            ]
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["relationship_id"]


async def test_relationships_etag_round_trips_to_304(client, db, read_key):
    aid = await _assignment(db)
    first = await client.get(_rels(aid), headers={"X-API-Key": read_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    r = await client.get(_rels(aid), headers={"X-API-Key": read_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["vary"] == "X-API-Key"


async def test_relationships_empty_collection_revalidates(client, db, read_key):
    aid = await _assignment(db)
    first = await client.get(_rels(aid), headers={"X-API-Key": read_key})
    assert "last-modified" not in first.headers
    r = await client.get(
        _rels(aid), headers={"X-API-Key": read_key, "If-None-Match": first.headers["etag"]}
    )
    assert r.status_code == 304


async def test_relationships_etag_changes_on_new_edge_from_either_direction(
    client, db, write_key, read_key
):
    """The read spans both directions, so an inbound edge must move the tag too."""
    frm, to = await _assignment(db), await _assignment(db)
    before = (await client.get(_rels(to), headers={"X-API-Key": read_key})).headers["etag"]
    await _edge(client, write_key, frm, to)
    after = await client.get(_rels(to), headers={"X-API-Key": read_key, "If-None-Match": before})
    assert after.status_code == 200


async def test_relationships_etag_changes_on_retract(client, db, write_key, read_key):
    frm, to = await _assignment(db), await _assignment(db)
    rid = await _edge(client, write_key, frm, to)
    before = (await client.get(_rels(frm), headers={"X-API-Key": read_key})).headers["etag"]

    r = await client.post(
        OBS,
        headers={"X-API-Key": write_key},
        json={"relationships": [{"op": "retract", "pm_relationship_id": rid}]},
    )
    assert r.status_code == 200, r.text

    after = await client.get(_rels(frm), headers={"X-API-Key": read_key, "If-None-Match": before})
    assert after.status_code == 200, "retracted edge still revalidated as unchanged"


async def test_relationships_etag_is_per_filter_and_per_window(client, db, write_key, read_key):
    frm, to = await _assignment(db), await _assignment(db)
    await _edge(client, write_key, frm, to)

    async def tag(query: str) -> str:
        r = await client.get(f"{_rels(frm)}{query}", headers={"X-API-Key": read_key})
        assert r.status_code == 200
        return r.headers["etag"]

    plain = await tag("")
    assert await tag("?include_archived=true") != plain
    assert await tag("?limit=5") != plain
    assert await tag("?offset=1") != plain
