"""#808: role identifiers on the public API — detail, lookup, observation writes.

A role carries `role_cannabis_observer` (external) since #808. The CLI reads it
back on `GET /roles/{id}`, finds a role by it with the `identifier_type` +
`identifier_value` filter on `listRoles`, and attaches it through
`additional_identifiers` on a role observation addressed by `pm_role_id`.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.api.public import roles as roles_route
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

_LIST = "/api/v1/roles"
_OBS = "/api/v1/roles/observations"
_TYPE = "role_cannabis_observer"


async def _key(db, *scopes: str) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Role Identifier Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    for scope in scopes:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description)"
            " VALUES ($1, $1, $1) ON CONFLICT (id) DO NOTHING",
            scope,
        )
        await db.execute(
            "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, scope
        )
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def read_headers(db):
    return {"X-API-Key": await _key(db)}


@pytest_asyncio.fixture(loop_scope="session")
async def write_headers(db):
    return {"X-API-Key": await _key(db, "observations:write")}


@pytest_asyncio.fixture(loop_scope="session")
async def org(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _role(db, org_id, title="Chair", *, archived=False) -> str:
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, archived_at)"
        " VALUES ($1, $2, $3, CASE WHEN $4 THEN NOW() END)",
        rid,
        org_id,
        title,
        archived,
    )
    return rid


async def _identify(db, entity_id, value, slug=_TYPE) -> str:
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, id, $3 FROM entity_identifier_types WHERE slug = $4",
        iid,
        entity_id,
        value,
        slug,
    )
    return iid


async def _held(db, entity_id) -> list[str]:
    return [
        r["value"]
        for r in await db.fetch(
            "SELECT i.value FROM identifiers i"
            " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
            " WHERE t.slug = $1 AND i.entity_id = $2 ORDER BY i.value",
            _TYPE,
            entity_id,
        )
    ]


# --- GET /roles/{id} -------------------------------------------------------


async def test_detail_carries_identifiers(client, db, read_headers, org):
    rid = await _role(db, org)
    iid = await _identify(db, rid, "01SITEROLE")
    type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", _TYPE)

    r = await client.get(f"{_LIST}/{rid}", headers=read_headers)

    assert r.status_code == 200
    assert r.json()["identifiers"] == [
        {"id": iid, "type_id": type_id, "type_slug": _TYPE, "value": "01SITEROLE"}
    ]


async def test_detail_identifiers_empty_by_default(client, db, read_headers, org):
    r = await client.get(f"{_LIST}/{await _role(db, org)}", headers=read_headers)
    assert r.json()["identifiers"] == []


# --- GET /roles?identifier_type=&identifier_value= -------------------------


def _lookup(value, slug=_TYPE, **extra):
    return {"identifier_type": slug, "identifier_value": value, **extra}


async def test_lookup_finds_the_role(client, db, read_headers, org):
    rid = await _role(db, org)
    await _role(db, org, "Vice Chair")
    await _identify(db, rid, "01FINDME")

    r = await client.get(_LIST, params=_lookup("01FINDME"), headers=read_headers)

    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body["data"]] == [rid]
    assert body["meta"]["count"] == 1
    assert body["meta"]["has_more"] is False


async def test_lookup_returns_every_holder_not_one(client, db, read_headers, org):
    """No (type, value) uniqueness: a duplicate link shows as two rows, never a silent pick."""
    a, b = await _role(db, org, "A"), await _role(db, org, "B")
    await _identify(db, a, "01DUP")
    await _identify(db, b, "01DUP")

    r = await client.get(_LIST, params=_lookup("01DUP"), headers=read_headers)

    assert sorted(d["id"] for d in r.json()["data"]) == sorted([a, b])


async def test_lookup_ands_with_organization_id(client, db, read_headers, org):
    rid = await _role(db, org)
    await _identify(db, rid, "01SCOPED")

    r = await client.get(
        _LIST, params=_lookup("01SCOPED", organization_id=generate_id()), headers=read_headers
    )

    assert r.json()["data"] == []


async def test_lookup_honours_include_archived(client, db, read_headers, org):
    rid = await _role(db, org, archived=True)
    await _identify(db, rid, "01ARCHIVED")

    hidden = await client.get(_LIST, params=_lookup("01ARCHIVED"), headers=read_headers)
    shown = await client.get(
        _LIST, params=_lookup("01ARCHIVED", include_archived="true"), headers=read_headers
    )

    assert hidden.json()["data"] == []
    assert [d["id"] for d in shown.json()["data"]] == [rid]


async def test_lookup_by_another_entity_types_slug_matches_nothing(client, db, read_headers, org):
    """The same value under an assignment type is not a role's identifier."""
    rid = await _role(db, org)
    await _identify(db, rid, "01CROSS")

    r = await client.get(
        _LIST,
        params=_lookup("01CROSS", slug="role_assignment_cannabis_observer"),
        headers=read_headers,
    )

    assert r.json()["data"] == []


async def test_lookup_by_an_unknown_slug_matches_nothing(client, read_headers):
    r = await client.get(_LIST, params=_lookup("x", slug="no_such_type"), headers=read_headers)
    assert r.status_code == 200
    assert r.json()["data"] == []


@pytest.mark.parametrize(
    "params",
    [{"identifier_type": _TYPE}, {"identifier_value": "x"}, _lookup(" ")],
)
async def test_lookup_needs_both_halves(client, read_headers, params):
    r = await client.get(_LIST, params=params, headers=read_headers)
    assert r.status_code == 422


# --- POST /roles/observations additional_identifiers -----------------------


def _extra(value, slug=_TYPE):
    return {"additional_identifiers": [{"identifier_type_slug": slug, "identifier_value": value}]}


async def test_pm_addressed_observation_attaches_the_identifier(client, db, write_headers, org):
    rid = await _role(db, org)

    r = await client.post(
        _OBS,
        json={"identifier_type": "pm_role_id", "identifier_value": rid, **_extra("01ATTACH")},
        headers=write_headers,
    )

    assert r.json()["disposition"] == "auto-attached"
    assert await _held(db, rid) == ["01ATTACH"]


async def test_natural_key_create_attaches_the_identifier(client, db, write_headers, org):
    r = await client.post(
        _OBS,
        json={"organization_id": org, "title": "Clerk", **_extra("01NEW")},
        headers=write_headers,
    )

    body = r.json()
    assert body["disposition"] == "new"
    assert await _held(db, body["entity_id"]) == ["01NEW"]


async def test_resending_the_same_value_is_a_no_op(client, db, write_headers, org):
    rid = await _role(db, org)
    payload = {"identifier_type": "pm_role_id", "identifier_value": rid, **_extra("01SAME")}

    await client.post(_OBS, json=payload, headers=write_headers)
    r = await client.post(_OBS, json=payload, headers=write_headers)

    assert r.json()["disposition"] == "auto-attached"
    assert await _held(db, rid) == ["01SAME"]


async def test_a_different_value_is_an_identifier_conflict(client, db, write_headers, org):
    rid = await _role(db, org)
    await _identify(db, rid, "01FIRST")

    r = await client.post(
        _OBS,
        json={"identifier_type": "pm_role_id", "identifier_value": rid, **_extra("01SECOND")},
        headers=write_headers,
    )

    assert r.json() == {
        **r.json(),
        "disposition": "rejected",
        "reason": f"identifier_conflict: '{_TYPE}'",
    }
    assert await _held(db, rid) == ["01FIRST"]


async def test_an_internal_type_is_refused(client, db, write_headers, org):
    rid = await _role(db, org)

    r = await client.post(
        _OBS,
        json={
            "identifier_type": "pm_role_id",
            "identifier_value": rid,
            **_extra(rid, "pm_role_id"),
        },
        headers=write_headers,
    )

    assert r.json()["disposition"] == "rejected"
    assert "Internal identifier type" in r.json()["reason"]


async def test_a_rejected_identifier_leaves_no_new_role_behind(client, db, write_headers, org):
    """#456 atomicity on roles: resolution created the role, the payload then rejected."""
    r = await client.post(
        _OBS,
        json={
            "organization_id": org,
            "title": "Ghost Seat",
            **_extra("01X", "person_cannabis_observer"),
        },
        headers=write_headers,
    )

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "identifier_type_mismatch: 'person_cannabis_observer'"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM roles WHERE organization_id=$1 AND title='Ghost Seat'", org
        )
        == 0
    )


async def test_a_rejected_link_leaves_no_new_role_behind(client, db, write_headers, org):
    """The same hole predates #808: a payload write failing after create kept the role."""
    r = await client.post(
        _OBS,
        json={
            "organization_id": org,
            "title": "Linkless Seat",
            "links": [{"url": "https://example.org/x", "link_type_slug": "no_such_link_type"}],
        },
        headers=write_headers,
    )

    assert r.json()["disposition"] == "rejected"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM roles WHERE organization_id=$1 AND title='Linkless Seat'", org
        )
        == 0
    )


# --- resolve_entity knows the role table ------------------------------------


async def test_a_live_roles_identifier_on_another_door_is_a_mismatch_not_archived(
    client, db, write_headers, org
):
    """Addressing /people/observations by a live role's identifier names the kind.

    Without `role` in resolve_entity's table map, the liveness check found no
    table and mislabelled the live role `role_archived`.
    """
    rid = await _role(db, org)
    await _identify(db, rid, "01LIVEROLE")

    r = await client.post(
        "/api/v1/people/observations",
        json={"identifier_type": _TYPE, "identifier_value": "01LIVEROLE"},
        headers=write_headers,
    )

    assert r.json()["reason"] == "entity_type_mismatch: 'role'"


async def test_an_unknown_role_identifier_never_creates(client, write_headers):
    r = await client.post(
        "/api/v1/people/observations",
        json={"identifier_type": _TYPE, "identifier_value": "01NOBODY"},
        headers=write_headers,
    )

    assert r.json()["reason"] == "entity_type_not_creatable: 'role'"


# --- the create race, inside the observation transaction --------------------


async def test_a_lost_create_race_attaches_and_still_writes_the_payload(
    client, db, write_headers, org, monkeypatch
):
    """#808 moved resolution inside the write transaction; the race retry must survive it.

    The first resolve hits a real unique violation on uq_role_org_title. Its
    savepoint rolls back alone (taking the stand-in twin with it), so the retry
    resolves and the payload still writes — without the savepoint, the aborted
    observation transaction would fail every statement after it.
    """
    real_resolve = roles_route.resolve_role
    calls = 0

    async def racing_resolve(conn, organization_id, title, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                generate_id(),
                organization_id,
                title,
            )
            await conn.execute(  # the loser's own insert: a real UniqueViolation
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                generate_id(),
                organization_id,
                title,
            )
        return await real_resolve(conn, organization_id, title, **kwargs)

    monkeypatch.setattr(roles_route, "resolve_role", racing_resolve)

    r = await client.post(
        _OBS,
        json={"organization_id": org, "title": "Raced", **_extra("01RACE")},
        headers=write_headers,
    )

    body = r.json()
    assert calls == 2
    assert body["disposition"] == "new"  # the twin rolled back with the first attempt
    assert await _held(db, body["entity_id"]) == ["01RACE"]
