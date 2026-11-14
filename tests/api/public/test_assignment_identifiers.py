"""#809: role-assignment identifiers on the public API — detail, lookup, writes.

The database side has carried assignment identifiers since #326; this is the
wire half, shaped like the role half of #808: `identifiers` on `GET
/assignments/{id}`, the `identifier_type` + `identifier_value` filter on
`listAssignments`, and `additional_identifiers` on assignment observations.
"""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

_LIST = "/api/v1/assignments"
_OBS = "/api/v1/assignments/observations"
_TYPE = "role_assignment_cannabis_observer"


async def _key(db, *scopes: str) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Assignment Identifier Key",
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
async def seat(db):
    """A person and a role to hang assignments on."""
    oid, rid, pid = generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return {"person_id": pid, "role_id": rid}


async def _assignment(db, seat, start="2020-01-01", *, archived=False) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, archived_at)"
        " VALUES ($1, $2, $3, $4, CASE WHEN $5 THEN NOW() END)",
        aid,
        seat["person_id"],
        seat["role_id"],
        date.fromisoformat(start),
        archived,
    )
    return aid


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


# --- GET /assignments/{id} -------------------------------------------------


async def test_detail_carries_external_identifiers_only(client, db, read_headers, seat):
    aid = await _assignment(db, seat)
    iid = await _identify(db, aid, "01SITERA")
    await _identify(db, aid, aid, "pm_assignment_id")  # internal: never listed
    type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", _TYPE)

    r = await client.get(f"{_LIST}/{aid}", headers=read_headers)

    assert r.status_code == 200
    assert r.json()["identifiers"] == [
        {"id": iid, "type_id": type_id, "type_slug": _TYPE, "value": "01SITERA"}
    ]


# --- GET /assignments?identifier_type=&identifier_value= -------------------


def _lookup(value, slug=_TYPE, **extra):
    return {"identifier_type": slug, "identifier_value": value, **extra}


async def test_lookup_finds_the_assignment(client, db, read_headers, seat):
    aid = await _assignment(db, seat)
    await _assignment(db, seat, "2022-01-01")
    await _identify(db, aid, "01FINDRA")

    r = await client.get(_LIST, params=_lookup("01FINDRA"), headers=read_headers)

    assert r.status_code == 200
    assert [d["id"] for d in r.json()["data"]] == [aid]
    assert r.json()["meta"]["has_more"] is False


async def test_lookup_returns_every_holder_not_one(client, db, read_headers, seat):
    """Prod holds role_wa_pdc values on more than one assignment: show them all."""
    a, b = await _assignment(db, seat), await _assignment(db, seat, "2022-01-01")
    await _identify(db, a, "4242", "role_wa_pdc")
    await _identify(db, b, "4242", "role_wa_pdc")

    r = await client.get(_LIST, params=_lookup("4242", slug="role_wa_pdc"), headers=read_headers)

    assert [d["id"] for d in r.json()["data"]] == [a, b]  # (person, role, start, id)


async def test_lookup_ands_with_person_id(client, db, read_headers, seat):
    aid = await _assignment(db, seat)
    await _identify(db, aid, "01SCOPEDRA")

    r = await client.get(
        _LIST, params=_lookup("01SCOPEDRA", person_id=generate_id()), headers=read_headers
    )

    assert r.json()["data"] == []


async def test_lookup_honours_include_archived(client, db, read_headers, seat):
    aid = await _assignment(db, seat, archived=True)
    await _identify(db, aid, "01ARCHRA")

    hidden = await client.get(_LIST, params=_lookup("01ARCHRA"), headers=read_headers)
    shown = await client.get(
        _LIST, params=_lookup("01ARCHRA", include_archived="true"), headers=read_headers
    )

    assert hidden.json()["data"] == []
    assert [d["id"] for d in shown.json()["data"]] == [aid]


async def test_lookup_by_a_role_types_slug_matches_nothing(client, db, read_headers, seat):
    aid = await _assignment(db, seat)
    await _identify(db, aid, "01CROSSRA")

    r = await client.get(
        _LIST, params=_lookup("01CROSSRA", slug="role_cannabis_observer"), headers=read_headers
    )

    assert r.json()["data"] == []


@pytest.mark.parametrize("params", [{"identifier_type": _TYPE}, {"identifier_value": "x"}])
async def test_lookup_needs_both_halves(client, read_headers, params):
    r = await client.get(_LIST, params=params, headers=read_headers)
    assert r.status_code == 422


# --- POST /assignments/observations additional_identifiers -----------------


def _extra(value, slug=_TYPE):
    return {"additional_identifiers": [{"identifier_type_slug": slug, "identifier_value": value}]}


def _by_id(aid, **extra):
    return {"identifier_type": "pm_assignment_id", "identifier_value": aid, **extra}


async def test_pm_addressed_observation_attaches_the_identifier(client, db, write_headers, seat):
    aid = await _assignment(db, seat)

    r = await client.post(_OBS, json=_by_id(aid, **_extra("01ATTACHRA")), headers=write_headers)

    assert r.json()["disposition"] == "auto-attached"
    assert await _held(db, aid) == ["01ATTACHRA"]


async def test_natural_key_create_attaches_the_identifier(client, db, write_headers, seat):
    r = await client.post(
        _OBS, json={**seat, "start_date": "2024-02-02", **_extra("01NEWRA")}, headers=write_headers
    )

    body = r.json()
    assert body["disposition"] == "new"
    assert await _held(db, body["entity_id"]) == ["01NEWRA"]


async def test_resending_the_same_value_is_a_no_op(client, db, write_headers, seat):
    aid = await _assignment(db, seat)
    payload = _by_id(aid, **_extra("01SAMERA"))

    await client.post(_OBS, json=payload, headers=write_headers)
    r = await client.post(_OBS, json=payload, headers=write_headers)

    assert r.json()["disposition"] == "auto-attached"
    assert await _held(db, aid) == ["01SAMERA"]


async def test_a_different_value_is_an_identifier_conflict(client, db, write_headers, seat):
    aid = await _assignment(db, seat)
    await _identify(db, aid, "01FIRSTRA")

    r = await client.post(_OBS, json=_by_id(aid, **_extra("01SECONDRA")), headers=write_headers)

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == f"identifier_conflict: '{_TYPE}'"
    assert await _held(db, aid) == ["01FIRSTRA"]


async def test_an_internal_type_is_refused(client, db, write_headers, seat):
    aid = await _assignment(db, seat)

    r = await client.post(
        _OBS, json=_by_id(aid, **_extra(aid, "pm_assignment_id")), headers=write_headers
    )

    assert r.json()["disposition"] == "rejected"
    assert "Internal identifier type" in r.json()["reason"]


async def test_a_mismatched_type_rejects_and_creates_nothing(client, db, write_headers, seat):
    """#456: the natural-key create rolls back with the rejected identifier."""
    r = await client.post(
        _OBS,
        json={**seat, "start_date": "2025-03-03", **_extra("01X", "role_cannabis_observer")},
        headers=write_headers,
    )

    assert r.json()["reason"] == "identifier_type_mismatch: 'role_cannabis_observer'"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM role_assignments"
            " WHERE person_id=$1 AND role_id=$2 AND start_date='2025-03-03'",
            seat["person_id"],
            seat["role_id"],
        )
        == 0
    )


async def test_a_retracted_twin_withholds_the_identifier(client, db, write_headers, seat):
    """#391: the anti-resurrection attach writes nothing, and says so in `unapplied`."""
    created = await client.post(
        _OBS, json={**seat, "start_date": "2019-09-09"}, headers=write_headers
    )
    aid = created.json()["entity_id"]
    retracted = await client.post(_OBS, json=_by_id(aid, op="retract"), headers=write_headers)
    assert retracted.json()["disposition"] == "retracted"

    r = await client.post(
        _OBS, json={**seat, "start_date": "2019-09-09", **_extra("01GHOST")}, headers=write_headers
    )

    body = r.json()
    assert (body["disposition"], body["entity_id"], body["attached_archived"]) == (
        "auto-attached",
        aid,
        True,
    )
    assert body["unapplied"] == ["additional_identifiers"]
    assert await _held(db, aid) == []


async def test_a_retract_ignores_additional_identifiers(client, db, write_headers, seat):
    """A retract's refine payload is ignored; identifiers are payload too."""
    aid = await _assignment(db, seat)

    r = await client.post(
        _OBS, json=_by_id(aid, op="retract", **_extra("01IGNORED")), headers=write_headers
    )

    assert r.json()["disposition"] == "retracted"
    assert await _held(db, aid) == []


async def test_a_pm_id_naming_an_archived_assignment_is_not_found(client, db, write_headers, seat):
    """The archived holder answer for id-addressing: pm_id_not_found, not <type>_archived."""
    aid = await _assignment(db, seat, archived=True)

    r = await client.post(_OBS, json=_by_id(aid, **_extra("01ARCH")), headers=write_headers)

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"].startswith("pm_id_not_found")
    assert await _held(db, aid) == []
