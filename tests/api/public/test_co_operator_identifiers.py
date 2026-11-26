"""#879: a deliberate create of a person or organization by a producer-minted id.

``person_co_operator`` / ``org_co_operator`` are external identifier types whose
value is a ULID the cli mints. An unknown value creates the record (``new``); a
re-send of the same value attaches to it (``auto-attached``), for good. One value
names one record (``uq_identifiers_cannabis_observer``, #812), so a second
entity can't take it; after a timeout the producer recovers the id by searching
on the identifier.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

#: entity type → (observation route, minted type, search route, name payload)
CASES = {
    "person": (
        "/api/v1/people/observations",
        "person_co_operator",
        "/api/v1/people/search",
        {"name": "Pat Presenter", "is_canonical": True},
    ),
    "organization": (
        "/api/v1/orgs/observations",
        "org_co_operator",
        "/api/v1/orgs/search",
        {"name": "Neighbourhood Growers Collective", "is_canonical": True},
    ),
}
TABLES = {"person": "people", "organization": "organizations"}


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Co Operator Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await db.execute(
        "INSERT INTO api_key_scope_types (id, display_name, description)"
        " VALUES ('observations:write', 'w', 'w') ON CONFLICT (id) DO NOTHING"
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1, 'observations:write')", kid
    )
    return raw


def _minted() -> str:
    return generate_id()  # a ULID, as the cli mints


async def _create(client, key: str, entity_type: str, value: str):
    route, slug, _, name = CASES[entity_type]
    return await client.post(
        route,
        json={"identifier_type": slug, "identifier_value": value, "names": [name]},
        headers={"X-API-Key": key},
    )


async def test_the_types_are_seeded_external_and_one_per_entity_type(db):
    rows = await db.fetch(
        "SELECT slug, entity_type, is_internal FROM entity_identifier_types"
        " WHERE slug IN ('person_co_operator', 'org_co_operator') ORDER BY slug"
    )
    assert [tuple(r) for r in rows] == [
        ("org_co_operator", "organization", False),
        ("person_co_operator", "person", False),
    ]


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_an_unknown_minted_id_creates_the_record(client, db, write_key, entity_type):
    r = await _create(client, write_key, entity_type, _minted())

    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_type"] == entity_type
    table = TABLES[entity_type]
    assert await db.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", body["entity_id"])


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_a_re_send_attaches_to_the_same_record(client, write_key, entity_type):
    value = _minted()
    first = await _create(client, write_key, entity_type, value)

    again = await _create(client, write_key, entity_type, value)

    assert again.status_code == 200
    assert again.json()["disposition"] == "auto-attached"
    assert again.json()["entity_id"] == first.json()["entity_id"]


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_a_search_on_the_minted_id_recovers_the_record(client, write_key, entity_type):
    """After a timeout the producer doesn't know whether the create landed."""
    value = _minted()
    created = (await _create(client, write_key, entity_type, value)).json()["entity_id"]
    _, slug, search, _ = CASES[entity_type]

    r = await client.get(
        search,
        params={"identifier_type": slug, "identifier_value": value},
        headers={"X-API-Key": write_key},
    )

    assert r.status_code == 200
    assert [hit["id"] for hit in r.json()["data"]] == [created]


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_a_minted_id_another_record_holds_is_identifier_taken(
    client, db, write_key, entity_type
):
    value = _minted()
    holder = (await _create(client, write_key, entity_type, value)).json()["entity_id"]
    other = generate_id()  # no minted id of its own, else this is identifier_conflict
    await db.execute(f"INSERT INTO {TABLES[entity_type]} (id) VALUES ($1)", other)
    route, slug, _, _ = CASES[entity_type]
    pm_type = "pm_person_id" if entity_type == "person" else "pm_org_id"

    r = await client.post(
        route,
        json={
            "identifier_type": pm_type,
            "identifier_value": other,
            "additional_identifiers": [{"identifier_type_slug": slug, "identifier_value": value}],
        },
        headers={"X-API-Key": write_key},
    )

    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason_code"] == "identifier_taken"
    assert r.json()["reason"] == f"identifier_taken: {slug!r} held by {holder!r}"


async def _held_values(db, entity_id: str, slug: str) -> set[str]:
    rows = await db.fetch(
        "SELECT i.value FROM identifiers i JOIN entity_identifier_types t"
        " ON t.id = i.entity_identifier_type_id WHERE i.entity_id = $1 AND t.slug = $2",
        entity_id,
        slug,
    )
    return {r["value"] for r in rows}


@pytest.mark.parametrize("entity_type", sorted(CASES))
async def test_replaces_swaps_one_minted_id_for_another(client, db, write_key, entity_type):
    """#848: the co_operator types are one-holder types, so they take ``replaces``."""
    old, new = _minted(), _minted()
    created = (await _create(client, write_key, entity_type, old)).json()["entity_id"]
    route, slug, _, _ = CASES[entity_type]
    pm_type = "pm_person_id" if entity_type == "person" else "pm_org_id"

    r = await client.post(
        route,
        json={
            "identifier_type": pm_type,
            "identifier_value": created,
            "additional_identifiers": [
                {"identifier_type_slug": slug, "identifier_value": new, "replaces": old}
            ],
        },
        headers={"X-API-Key": write_key},
    )

    assert r.status_code == 200
    assert r.json()["disposition"] == "auto-attached"
    assert await _held_values(db, created, slug) == {new}
