"""The committed-row teardown helpers in ``tests/db_utils.py`` (#819).

The integration tier truncates its shared DB once, at session start, so a
fixture that commits rows owns removing them. These tests commit an entity
graph through a plain pool connection, purge it, and read every table it
touched back as empty.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id
from tests.db_utils import drop_committed_key, purge_committed_entities

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Autocommit: each write is committed, as a committing fixture's would be."""
    async with db_pool.acquire() as c:
        yield c


async def _graph(conn) -> dict[str, str]:
    """An org, a person, a role and an assignment, each with ancillary rows."""
    ids = {k: generate_id() for k in ("org", "other_org", "person", "role", "ra", "event")}
    await conn.execute(
        "INSERT INTO organizations (id) VALUES ($1), ($2)", ids["org"], ids["other_org"]
    )
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Purge 819 Org', TRUE)",
        generate_id(),
        ids["org"],
    )
    await conn.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'P819', TRUE)",
        generate_id(),
        ids["org"],
    )
    await conn.execute("INSERT INTO people (id) VALUES ($1)", ids["person"])
    name_id = generate_id()
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'P 819', TRUE)",
        name_id,
        ids["person"],
    )
    await conn.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'person_name', $2, $3)",
        generate_id(),
        name_id,
        "https://example.gov/819/name",
    )
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Purge 819 Role')",
        ids["role"],
        ids["org"],
    )
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        ids["ra"],
        ids["person"],
        ids["role"],
    )
    await conn.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " SELECT $1, 'role_assignment', $2, 'https://example.gov/819/ra', id"
        " FROM link_types WHERE slug = 'website'",
        generate_id(),
        ids["ra"],
    )
    # An event on the org naming the other org: the org must go first.
    await conn.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)
           SELECT $1, 'organization', $2, t.id, 'organization', $3
             FROM entity_event_types t WHERE t.slug = 'succeeded_by'""",
        ids["event"],
        ids["org"],
        ids["other_org"],
    )
    await conn.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'entity_event', $2, $3)",
        generate_id(),
        ids["event"],
        "https://example.gov/819/event",
    )
    await conn.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('organization', $1, $2)",
        ids["other_org"] + "X",
        ids["other_org"],
    )
    return ids


async def _left(conn, ids: dict[str, str]) -> dict[str, int]:
    every = list(ids.values()) + [ids["other_org"] + "X"]
    counts = {}
    for table, column in (
        ("organizations", "id"),
        ("organization_names", "organization_id"),
        ("organization_acronyms", "organization_id"),
        ("people", "id"),
        ("person_names", "person_id"),
        ("roles", "id"),
        ("role_assignments", "id"),
        ("links", "entity_id"),
        ("entity_events", "entity_id"),
        ("deleted_entities", "entity_id"),
    ):
        counts[table] = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {column} = ANY($1::text[])", every
        )
    counts["citations"] = await conn.fetchval(
        "SELECT count(*) FROM citations WHERE url LIKE 'https://example.gov/819/%'"
    )
    return counts


async def test_purge_removes_the_graph_and_all_it_owns(conn):
    ids = await _graph(conn)
    try:
        await purge_committed_entities(
            conn,
            [
                ("role_assignment", ids["ra"]),
                ("role", ids["role"]),
                ("organization", ids["org"]),
                ("organization", ids["other_org"]),
                ("organization", ids["other_org"] + "X"),  # merged away: tombstone only
                ("person", ids["person"]),
            ],
        )
    finally:
        left = await _left(conn, ids)
    assert left == dict.fromkeys(left, 0)


async def test_purge_refuses_an_unknown_type(conn):
    with pytest.raises(ValueError):
        await purge_committed_entities(conn, [("persn", generate_id())])


async def test_drop_committed_key_takes_its_log_rows(conn):
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await conn.execute("INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, f"{uid}@819.test")
    await conn.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Drop 819",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await conn.execute(
        "INSERT INTO api_request_log (api_key_id, method, path, route_group, status_code,"
        " latency_ms) VALUES ($1, 'GET', $2, 'other', 200, 1)",
        kid,
        f"/api/v1/orgs/{kid}",
    )

    await drop_committed_key(conn, kid, uid)

    assert await conn.fetchval("SELECT count(*) FROM api_keys WHERE id = $1", kid) == 0
    assert await conn.fetchval("SELECT count(*) FROM app_users WHERE id = $1", uid) == 0
    # Not orphaned to NULL by ON DELETE SET NULL: gone.
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM api_request_log WHERE path = $1", f"/api/v1/orgs/{kid}"
        )
        == 0
    )
