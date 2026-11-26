"""Integration tests for person names on POST /api/v1/people/observations (#845).

The wire half: the call Observo makes from operator review, the per-name
results, a citation riding on a name, and ``op="retract"``. The rules
themselves are covered in ``tests/core/test_person_name_observations.py``.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

_BASE = "/api/v1/people/observations"


async def _write_key(db, label: str) -> tuple[str, str]:
    """An API key holding observations:write, as (raw key, key id)."""
    scope_id = "observations:write"
    await db.execute(
        "INSERT INTO api_key_scope_types (id, display_name, description)"
        " VALUES ($1, 'Observations Write', 'Create and update observations')"
        " ON CONFLICT (id) DO NOTHING",
        scope_id,
    )
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{kid.lower()}@names.test"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1, $2)", kid, scope_id
    )
    return raw, kid


@pytest_asyncio.fixture(loop_scope="session")
async def observo_key(db):
    """Observo's key: observations:write, as production's holds."""
    return await _write_key(db, "Observo Names Test")


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    """A person as production holds them: one curated, canonical legal name."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Jodi Example', 'legal', TRUE)",
        generate_id(),
        pid,
    )
    return pid


def _post(client, raw_key: str, person_id: str, names: list[dict]):
    return client.post(
        _BASE,
        json={"identifier_type": "pm_person_id", "identifier_value": person_id, "names": names},
        headers={"X-API-Key": raw_key},
    )


_VARIANT = {"name": "Jody Example", "name_type": "variant"}
_CITATION = {"field_name": "name", "url": "https://hearings.example/42", "title": "Hearing 42"}


# ---------------------------------------------------------------------------
# The call Observo makes (#845 "What Observo would send")
# ---------------------------------------------------------------------------


async def test_observos_call_adds_a_public_variant_stamped_with_its_key(
    client, observo_key, person_id, db
):
    raw, kid = observo_key

    r = await _post(client, raw, person_id, [_VARIANT])

    assert r.status_code == 200
    body = r.json()
    assert (body["disposition"], body["entity_id"]) == ("auto-attached", person_id)
    row = await db.fetchrow(
        "SELECT name_type, visibility, is_canonical, source_key_id FROM person_names"
        " WHERE person_id=$1 AND name='Jody Example'",
        person_id,
    )
    assert dict(row) == {
        "name_type": "variant",
        "visibility": "public",
        "is_canonical": False,
        "source_key_id": kid,
    }


async def test_observos_variant_leaves_the_displayed_name_alone(client, observo_key, person_id, db):
    raw, _ = observo_key

    await _post(client, raw, person_id, [_VARIANT])

    assert (
        await db.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", person_id
        )
        == "Jodi Example"
    )


async def test_observos_variant_is_readable_as_an_accepted_spelling(client, observo_key, person_id):
    raw, _ = observo_key
    await _post(client, raw, person_id, [_VARIANT])

    r = await client.get(f"/api/v1/people/{person_id}", headers={"X-API-Key": raw})

    assert {(n["name"], n["name_type"], n["is_canonical"]) for n in r.json()["names"]} == {
        ("Jodi Example", "legal", True),
        ("Jody Example", "variant", False),
    }


# ---------------------------------------------------------------------------
# Per-name results
# ---------------------------------------------------------------------------


async def test_a_name_reports_new_then_auto_attached_with_one_id(client, observo_key, person_id):
    raw, _ = observo_key

    first = (await _post(client, raw, person_id, [_VARIANT])).json()["names"]
    second = (await _post(client, raw, person_id, [_VARIANT])).json()["names"]

    assert [n["disposition"] for n in first + second] == ["new", "auto-attached"]
    assert first[0]["name_id"] == second[0]["name_id"] is not None


async def test_an_observation_without_names_reports_no_names(client, observo_key, person_id):
    raw, _ = observo_key

    r = await _post(client, raw, person_id, [])

    assert r.json()["names"] is None


async def test_a_deadname_reports_not_public_and_no_id(client, observo_key, person_id):
    raw, _ = observo_key

    r = await _post(client, raw, person_id, [{"name": "Old Example", "name_type": "deadname"}])

    assert r.json()["names"] == [{"disposition": "not-public", "name_id": None, "citations": None}]


# ---------------------------------------------------------------------------
# A citation on the name
# ---------------------------------------------------------------------------


async def test_a_citation_rides_on_the_name_in_one_call(client, observo_key, person_id, db):
    raw, _ = observo_key

    r = await _post(client, raw, person_id, [{**_VARIANT, "citations": [_CITATION]}])

    (name,) = r.json()["names"]
    (citation,) = name["citations"]
    assert citation["disposition"] == "new"
    row = await db.fetchrow(
        "SELECT entity_type, entity_id, url FROM citations WHERE id=$1", citation["citation_id"]
    )
    assert dict(row) == {
        "entity_type": "person_name",
        "entity_id": name["name_id"],
        "url": "https://hearings.example/42",
    }


async def test_a_rejected_name_citation_writes_nothing(client, observo_key, person_id, db):
    raw, _ = observo_key

    r = await _post(
        client, raw, person_id, [{**_VARIANT, "citations": [{**_CITATION, "field_name": "notes"}]}]
    )

    assert (r.json()["disposition"], r.json()["reason"]) == ("rejected", "citable_field_unknown")
    assert (
        await db.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name='Jody Example'",
            person_id,
        )
        == 0
    )


# ---------------------------------------------------------------------------
# op="retract"
# ---------------------------------------------------------------------------


async def test_retract_takes_back_the_name_the_key_added(client, observo_key, person_id, db):
    raw, _ = observo_key
    added = (await _post(client, raw, person_id, [_VARIANT])).json()["names"][0]["name_id"]

    r = await _post(client, raw, person_id, [{**_VARIANT, "op": "retract"}])

    assert r.json()["names"] == [{"disposition": "retracted", "name_id": added, "citations": None}]
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE id=$1", added) == 0


async def test_a_retracted_name_leaves_the_read(client, observo_key, person_id):
    raw, _ = observo_key
    await _post(client, raw, person_id, [_VARIANT])

    await _post(client, raw, person_id, [{**_VARIANT, "op": "retract"}])

    r = await client.get(f"/api/v1/people/{person_id}", headers={"X-API-Key": raw})
    assert [n["name"] for n in r.json()["names"]] == ["Jodi Example"]


async def test_retract_of_another_keys_name_rejects_and_keeps_it(
    client, observo_key, person_id, db
):
    raw, _ = observo_key
    other_raw, _ = await _write_key(db, "Another Producer")
    await _post(client, other_raw, person_id, [_VARIANT])

    r = await _post(client, raw, person_id, [{**_VARIANT, "op": "retract"}])

    assert (r.json()["disposition"], r.json()["reason"]) == ("rejected", "name_provenance_conflict")
    assert r.json()["reason_code"] == "provenance_conflict"  # #882: one name for the gate
    assert (
        await db.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND name='Jody Example'",
            person_id,
        )
        == 1
    )


async def test_a_rejected_observation_reports_no_names(client, observo_key, person_id):
    raw, _ = observo_key

    r = await _post(
        client, raw, person_id, [{"name": "Jodi Example", "name_type": "legal", "op": "retract"}]
    )

    assert (r.json()["disposition"], r.json()["names"]) == ("rejected", None)


async def test_a_rejected_retract_rolls_back_the_names_beside_it(
    client, observo_key, person_id, db
):
    """Embedded is all-or-nothing, as events and citations are."""
    raw, _ = observo_key

    r = await _post(
        client,
        raw,
        person_id,
        [
            {"name": "Jo Example", "name_type": "variant"},
            {"name": "Jodi Example", "name_type": "legal", "op": "retract"},
        ],
    )

    assert r.json()["disposition"] == "rejected"
    assert await db.fetchval("SELECT count(*) FROM person_names WHERE person_id=$1", person_id) == 1


async def test_observe_and_retract_of_one_name_is_a_422(client, observo_key, person_id):
    raw, _ = observo_key

    r = await _post(client, raw, person_id, [_VARIANT, {**_VARIANT, "op": "retract"}])

    assert r.status_code == 422
