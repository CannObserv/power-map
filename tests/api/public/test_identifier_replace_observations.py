"""#848: an observation replacing a ``*_cannabis_observer`` value through each route.

An ``additional_identifiers`` item naming ``replaces`` swaps that value for its
own in the observation's one transaction. The rules live in
``write_additional_identifiers`` (``tests/core/test_observation_writers.py``
holds each case); these cross the seam: the field survives each route's request
model, and each route answers the swap and its rejections alike.
"""

import pytest
import pytest_asyncio

from src.api.main import app
from tests.api.public.test_identifier_taken_observations import (
    ROUTES,
    UNIQUE_SLUGS,
    _entity,
    _identify,
    _key,
)
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def write_headers(db):
    return {"X-API-Key": await _key(db, "observations:write")}


async def _values(db, entity_id: str) -> list[str]:
    rows = await db.fetch(
        "SELECT value FROM identifiers WHERE entity_id = $1 ORDER BY value", entity_id
    )
    return [r["value"] for r in rows]


async def _replace(client, headers, entity_type: str, target: str, new: str, old: str):
    path, pm_type = ROUTES[entity_type]
    payload = {
        "identifier_type": pm_type,
        "identifier_value": target,
        "additional_identifiers": [
            {
                "identifier_type_slug": UNIQUE_SLUGS[entity_type],
                "identifier_value": new,
                "replaces": old,
            }
        ],
    }
    return await client.post(path, json=payload, headers=headers)


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_swaps_the_value_and_reads_as_an_attach(
    client,
    db,
    write_headers,
    entity_type,
):
    target = await _entity(db, entity_type)
    await _identify(db, target, UNIQUE_SLUGS[entity_type], "01OLD")

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.status_code == 200
    assert r.json()["disposition"] == "auto-attached"
    assert r.json()["entity_id"] == target
    assert await _values(db, target) == ["01NEW"]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_sent_twice_is_a_noop_the_second_time(
    client,
    db,
    write_headers,
    entity_type,
):
    target = await _entity(db, entity_type)
    await _identify(db, target, UNIQUE_SLUGS[entity_type], "01OLD")
    await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.json()["disposition"] == "auto-attached"
    assert await _values(db, target) == ["01NEW"]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_naming_a_value_not_held_is_rejected(
    client,
    db,
    write_headers,
    entity_type,
):
    target = await _entity(db, entity_type)
    await _identify(db, target, UNIQUE_SLUGS[entity_type], "01SOMEONE-ELSES")

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == f"replaces_not_held: '{UNIQUE_SLUGS[entity_type]}'"
    assert await _values(db, target) == ["01SOMEONE-ELSES"]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_with_a_taken_new_value_keeps_the_old_one(
    client,
    db,
    write_headers,
    entity_type,
):
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, UNIQUE_SLUGS[entity_type], "01NEW")
    await _identify(db, target, UNIQUE_SLUGS[entity_type], "01OLD")

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == (
        f"identifier_taken: '{UNIQUE_SLUGS[entity_type]}' held by '{holder}'"
    )
    assert await _values(db, target) == ["01OLD"]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_on_an_archived_entity_is_not_found(
    client,
    db,
    write_headers,
    entity_type,
):
    """Resolution refuses a ``pm_*``-addressed archived entity before any identifier write."""
    target = await _entity(db, entity_type, archived=True)
    await _identify(db, target, UNIQUE_SLUGS[entity_type], "01OLD")

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == f"pm_id_not_found: '{target}'"
    assert await _values(db, target) == ["01OLD"]


async def test_a_replace_on_a_jurisdiction_is_unsupported(client, db, write_headers):
    """The item model is shared with jurisdictions, which have no such type."""
    target = await archived_entity(db, "jurisdiction")
    await db.execute("UPDATE jurisdictions SET archived_at = NULL WHERE id = $1", target)
    await _identify(db, target, "jur_fips", "53033")
    await _identify(db, target, "jur_ocd", "ocd-old")
    payload = {
        "identifier_type": "jur_fips",
        "identifier_value": "53033",
        "additional_identifiers": [
            {
                "identifier_type_slug": "jur_ocd",
                "identifier_value": "ocd-new",
                "replaces": "ocd-old",
            }
        ],
    }

    r = await client.post("/api/v1/jurisdictions/observations", json=payload, headers=write_headers)

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "replaces_unsupported: 'jur_ocd'"
    assert await _values(db, target) == ["53033", "ocd-old"]


def test_the_item_schema_documents_replaces():
    item = app.openapi()["components"]["schemas"]["ObservationAdditionalIdentifier"]

    assert "replaces" in item["properties"]
    assert "replaces" not in item.get("required", [])
