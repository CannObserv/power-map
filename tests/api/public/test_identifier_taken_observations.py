"""#812: an observation attaching a ``*_cannabis_observer`` value another entity holds.

The pre-check in ``write_additional_identifiers`` rejects it
``identifier_taken: '<slug>' held by '<id>'`` (``held by archived '<id>'`` for an
archived holder, #481). A writer that commits the value between that check and
the ``INSERT`` loses on ``uq_identifiers_cannabis_observer``; all five
observation routes map that ``unique_violation`` to the same reason, never the
generic ``db_constraint_violation``. Either way nothing is written.
"""

import ast
import hashlib
import inspect
import os

import pytest
import pytest_asyncio

from src.api.public import assignments, jurisdictions, orgs, people, roles
from src.core import observation
from src.core.db import generate_id
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]

ROUTES = {
    "person": ("/api/v1/people/observations", "pm_person_id"),
    "organization": ("/api/v1/orgs/observations", "pm_org_id"),
    "role": ("/api/v1/roles/observations", "pm_role_id"),
    "role_assignment": ("/api/v1/assignments/observations", "pm_assignment_id"),
}
UNIQUE_SLUGS = {
    "organization": "org_cannabis_observer",
    "person": "person_cannabis_observer",
    "role": "role_cannabis_observer",
    "role_assignment": "role_assignment_cannabis_observer",
}
TABLES = {
    "organization": "organizations",
    "person": "people",
    "role": "roles",
    "role_assignment": "role_assignments",
}


async def _key(db, *scopes: str) -> str:
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Identifier Taken Key",
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
async def write_headers(db):
    return {"X-API-Key": await _key(db, "observations:write")}


async def _entity(db, entity_type: str, *, archived: bool = False) -> str:
    eid = await archived_entity(db, entity_type)
    if not archived:
        await db.execute(f"UPDATE {TABLES[entity_type]} SET archived_at = NULL WHERE id = $1", eid)
    return eid


async def _identify(db, entity_id: str, slug: str, value: str) -> None:
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, id, $3 FROM entity_identifier_types WHERE slug = $4",
        generate_id(),
        entity_id,
        value,
        slug,
    )


async def _held(db, entity_id: str) -> int:
    return await db.fetchval("SELECT count(*) FROM identifiers WHERE entity_id = $1", entity_id)


async def _observe(client, headers, entity_type: str, target: str, value: str):
    path, pm_type = ROUTES[entity_type]
    payload = {
        "identifier_type": pm_type,
        "identifier_value": target,
        "additional_identifiers": [
            {"identifier_type_slug": UNIQUE_SLUGS[entity_type], "identifier_value": value}
        ],
    }
    return await client.post(path, json=payload, headers=headers)


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_held_value_is_rejected_naming_the_holder(client, db, write_headers, entity_type):
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, UNIQUE_SLUGS[entity_type], "01SITEULID")

    r = await _observe(client, write_headers, entity_type, target, "01SITEULID")

    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == (
        f"identifier_taken: '{UNIQUE_SLUGS[entity_type]}' held by '{holder}'"
    )
    assert await _held(db, target) == 0


async def test_an_archived_holder_is_named_archived(client, db, write_headers):
    holder = await _entity(db, "organization", archived=True)
    target = await _entity(db, "organization")
    await _identify(db, holder, "org_cannabis_observer", "01SITEULID")

    r = await _observe(client, write_headers, "organization", target, "01SITEULID")

    assert r.json()["reason"] == (
        f"identifier_taken: 'org_cannabis_observer' held by archived '{holder}'"
    )


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_lost_race_answers_identifier_taken_not_a_generic_violation(
    client, db, write_headers, monkeypatch, entity_type
):
    """The holder commits after the pre-check reads: the INSERT meets the index."""

    async def _nobody(*_args, **_kwargs):
        return None

    monkeypatch.setattr(observation, "identifier_holder", _nobody)
    holder = await _entity(db, entity_type)
    target = await _entity(db, entity_type)
    await _identify(db, holder, UNIQUE_SLUGS[entity_type], "01SITEULID")

    r = await _observe(client, write_headers, entity_type, target, "01SITEULID")

    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == (
        f"identifier_taken: '{UNIQUE_SLUGS[entity_type]}' held by '{holder}'"
    )
    assert await _held(db, target) == 0


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_a_free_value_still_attaches(client, db, write_headers, entity_type):
    target = await _entity(db, entity_type)

    r = await _observe(client, write_headers, entity_type, target, "01FREE")

    assert r.json()["disposition"] == "auto-attached"
    assert await _held(db, target) == 1


# --- sweep: every observation route maps the race --------------------------------------


def _unique_violation_handlers(module) -> list[ast.ExceptHandler]:
    """``except`` clauses naming ``UniqueViolationError`` that answer an ObservationResponse."""
    tree = ast.parse(inspect.getsource(module))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if "UniqueViolationError" not in ast.unparse(node.type):
            continue
        if "ObservationResponse" in ast.unparse(node):
            found.append(node)
    return found


@pytest.mark.parametrize(
    "module", [people, orgs, roles, assignments, jurisdictions], ids=lambda m: m.__name__
)
def test_every_observation_route_maps_the_race_to_identifier_taken(module):
    handlers = _unique_violation_handlers(module)
    assert handlers, f"{module.__name__}: no UniqueViolationError handler answers the observation"
    for handler in handlers:
        assert "unique_violation_reason" in ast.unparse(handler), ast.unparse(handler)
