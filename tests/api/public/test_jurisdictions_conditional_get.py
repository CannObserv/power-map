"""Watermark *advancement* on the jurisdiction relationships read (#392 PR-C).

Separate module because these cases need the **committing** client. Postgres
`now()` is fixed at transaction start, so `updated_at` (set by the trigger) is
identical for every write inside one transaction — the rollback client freezes
it, and an assertion that a tag advances after an edit would pass vacuously or
fail spuriously (`docs/SCHEMA.md` § Core rules). The rest of the endpoint's
conditional-GET coverage lives in `test_jurisdictions.py` on the rollback
client, where count/content changes are enough.

What is being proven: an **in-place edit** of an edge — no row added or removed,
so the count is unchanged — still invalidates. That is the whole reason PR-C
added `jurisdiction_relationships.updated_at` + its trigger; before it, the
admin panel could edit an edge and every poller would 304 past the change
forever.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


# Committing (autocommit) variants: each write is its own transaction, so the
# trigger's NOW() actually advances between them. Rows leak but carry unique
# ULIDs (the test DB is session-truncated); the api_key is cleaned up.
@pytest_asyncio.fixture(loop_scope="session")
async def db(committing_db):
    return committing_db


@pytest_asyncio.fixture(loop_scope="session")
async def client(committing_client):
    return committing_client


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{kid}@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "jur conditional-get key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def edge(db):
    """Two jurisdictions joined by one edge. Yields (from_id, edge_id)."""
    a, b, rid = generate_id(), generate_id(), generate_id()
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='state'")
    rel_type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='is_fully_contained_by'"
    )
    for jid in (a, b):
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            f"cg-{jid}",
            f"Probe {jid}",
            type_id,
        )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        rid,
        a,
        b,
        rel_type_id,
    )
    yield a, rid
    await db.execute("DELETE FROM jurisdiction_relationships WHERE id=$1", rid)
    await db.execute("DELETE FROM jurisdictions WHERE id = ANY($1::text[])", [a, b])


async def test_in_place_edge_edit_invalidates_the_tag(client, db, api_key, edge):
    """No row added or removed — only `max(updated_at)` can catch this."""
    from_id, edge_id = edge
    url = f"/api/v1/jurisdictions/{from_id}/relationships"

    first = await client.get(url, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    before = first.headers["etag"]

    await db.execute(
        "UPDATE jurisdiction_relationships SET notes = 'edited by #392' WHERE id = $1", edge_id
    )

    after = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": before})
    assert after.status_code == 200, "in-place edge edit still revalidated as unchanged"
    assert after.headers["etag"] != before


async def test_supersede_invalidates_the_tag(client, db, api_key, edge):
    """Soft-retiring an edge is an in-place edit too — `superseded_at` is not a filter."""
    from_id, edge_id = edge
    url = f"/api/v1/jurisdictions/{from_id}/relationships"
    before = (await client.get(url, headers={"X-API-Key": api_key})).headers["etag"]

    await db.execute(
        "UPDATE jurisdiction_relationships SET superseded_at = NOW() WHERE id = $1", edge_id
    )

    after = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": before})
    assert after.status_code == 200
