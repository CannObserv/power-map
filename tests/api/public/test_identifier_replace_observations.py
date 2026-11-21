"""#848: an observation replacing a ``*_cannabis_observer`` value through each route.

An ``additional_identifiers`` item naming ``replaces`` swaps that value for its
own in the observation's one transaction. The rules live in
``write_additional_identifiers`` (``tests/core/test_observation_writers.py``
holds each case); these cross the seam: the field survives each route's request
model, and each route answers the swap and its rejections alike.
"""

import inspect

import pytest
import pytest_asyncio

from src.api.public import assignments, jurisdictions, orgs, people, roles
from src.core import observation
from src.core.db import generate_id
from tests.api.public.test_identifier_taken_observations import (
    ROUTES,
    TABLES,
    UNIQUE_SLUGS,
    _entity,
    _identify,
    _key,
)
from tests.db_utils import row_version
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
    before = await row_version(db, TABLES[entity_type], target)

    r = await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert r.json()["disposition"] == "auto-attached"
    assert await _values(db, target) == ["01NEW"]
    # A delete and re-insert would leave the same values but touch the parent.
    assert await row_version(db, TABLES[entity_type], target) == before


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
async def test_a_replace_losing_the_index_race_keeps_the_old_value(
    client, db, write_headers, monkeypatch, entity_type
):
    """The holder commits after the pre-check reads: the insert meets the index
    after the old value was deleted, and the rollback must bring it back."""

    async def _nobody(*_args, **_kwargs):
        return None

    monkeypatch.setattr(observation, "identifier_holder", _nobody)
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
    fips = f"fips-{generate_id()}"
    await _identify(db, target, "jur_fips", fips)
    await _identify(db, target, "jur_ocd", "ocd-old")
    payload = {
        "identifier_type": "jur_fips",
        "identifier_value": fips,
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
    assert await _values(db, target) == [fips, "ocd-old"]


def _replaced_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "identifier replaced" in r.getMessage()]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_an_applied_replace_is_logged_once(client, db, write_headers, caplog, entity_type):
    target = await _entity(db, entity_type)
    slug = UNIQUE_SLUGS[entity_type]
    await _identify(db, target, slug, "01OLD")

    with caplog.at_level("INFO", logger="src.core.observation"):
        await _replace(client, write_headers, entity_type, target, "01NEW", "01OLD")

    assert _replaced_lines(caplog) == [
        f"identifier replaced: {entity_type} {target} {slug} '01OLD' -> '01NEW'"
    ]


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_replace_rolled_back_by_a_later_item_is_not_logged(
    client, db, write_headers, caplog, entity_type
):
    """CR 1: the swap runs, a later item rejects, the observation rolls back —
    a log line then would record a replace that never happened."""
    target = await _entity(db, entity_type)
    slug = UNIQUE_SLUGS[entity_type]
    await _identify(db, target, slug, "01OLD")
    path, pm_type = ROUTES[entity_type]
    payload = {
        "identifier_type": pm_type,
        "identifier_value": target,
        "additional_identifiers": [
            {"identifier_type_slug": slug, "identifier_value": "01NEW", "replaces": "01OLD"},
            {"identifier_type_slug": "no_such_type", "identifier_value": "x"},
        ],
    }

    with caplog.at_level("INFO", logger="src.core.observation"):
        r = await client.post(path, json=payload, headers=write_headers)

    assert r.json()["disposition"] == "rejected"
    assert await _values(db, target) == ["01OLD"]
    assert _replaced_lines(caplog) == []


@pytest.mark.parametrize(
    "module", [people, orgs, roles, assignments, jurisdictions], ids=lambda m: m.__name__
)
def test_every_route_that_writes_identifiers_logs_its_replaces(module):
    """The writer cannot log (it runs before the commit), so each route must."""
    source = inspect.getsource(module)

    assert "write_additional_identifiers(" in source
    assert "log_replaced_identifiers(" in source
