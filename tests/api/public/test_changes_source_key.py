"""Integration tests for entity_changes.source_key_id stamping (#491).

The outbox row carries the API key whose request *transaction* caused it:
public write routes set the txn-local GUC ``app.source_key_id`` (via the
``stamped_transaction`` helper) and the change/touch triggers stamp
``NULLIF(current_setting('app.source_key_id', true), '')``. Admin, merge and
script writes never set the GUC, so their rows stay NULL. The feed exposes the
value as ``ChangeItem.source_key_id`` so a consumer can skip its own echoes.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_CHANGES = "/api/v1/changes"
_PEOPLE_OBS = "/api/v1/people/observations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db):
    """API key with observations:write scope; returns (raw_key, key_id)."""
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id=$1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "skid@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Source Key Stamp Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, scope_id
    )
    return raw, kid


async def _subscribe(db, key_id: str, entity_id: str, entity_type: str = "person") -> None:
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
        key_id,
        entity_id,
        entity_type,
    )


async def _outbox_rows(db, entity_id: str, after: int):
    return await db.fetch(
        "SELECT id, source_key_id FROM entity_changes WHERE entity_id=$1 AND id>$2 ORDER BY id",
        entity_id,
        after,
    )


# ---------------------------------------------------------------------------
# Trigger level — the GUC → outbox seam
# ---------------------------------------------------------------------------


async def test_trigger_stamps_guc_value(db):
    kid = generate_id()
    await db.execute("SELECT set_config('app.source_key_id', $1, true)", kid)
    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    rows = await _outbox_rows(db, person_id, before)
    assert rows and all(r["source_key_id"] == kid for r in rows)


async def test_trigger_unset_guc_is_null(db):
    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    rows = await _outbox_rows(db, person_id, before)
    assert rows and all(r["source_key_id"] is None for r in rows)


async def test_trigger_empty_string_guc_is_null(db):
    # A set-then-reverted custom GUC reads back as '' (not NULL) on a reused
    # session — the trigger's NULLIF must normalize it, or pooled connections
    # would stamp '' on admin/curator-origin rows.
    await db.execute("SELECT set_config('app.source_key_id', '', true)")
    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    rows = await _outbox_rows(db, person_id, before)
    assert rows and all(r["source_key_id"] is None for r in rows)


# ---------------------------------------------------------------------------
# Route seam — public write → stamped feed row on the wire
# ---------------------------------------------------------------------------


async def test_public_write_stamps_feed_row(client, db, write_key):
    raw, kid = write_key
    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    r = await client.post(
        _PEOPLE_OBS,
        json={
            "identifier_type": "person_wa_pdc",
            "identifier_value": "skid_" + os.urandom(6).hex(),
        },
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]
    await _subscribe(db, kid, entity_id)

    feed = await client.get(_CHANGES, params={"after": before}, headers={"X-API-Key": raw})
    assert feed.status_code == 200
    items = [i for i in feed.json()["data"] if i["entity_id"] == entity_id]
    assert items and all(i["source_key_id"] == kid for i in items)


async def test_curator_origin_row_is_null_on_wire(client, db, write_key):
    raw, kid = write_key
    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await _subscribe(db, kid, person_id)

    feed = await client.get(_CHANGES, params={"after": before}, headers={"X-API-Key": raw})
    assert feed.status_code == 200
    items = [i for i in feed.json()["data"] if i["entity_id"] == person_id]
    assert items and all(i["source_key_id"] is None for i in items)


async def test_stamp_does_not_leak_past_route_transaction(client, db, write_key):
    # Under the rollback client (#288) a route txn is a savepoint whose released
    # local GUC would persist to the end of the outer test transaction — and in
    # any reused session it reads back as ''. The helper's exit reset plus the
    # trigger's NULLIF keep a follow-up non-API write unattributed.
    raw, kid = write_key
    r = await client.post(
        _PEOPLE_OBS,
        json={
            "identifier_type": "person_wa_pdc",
            "identifier_value": "skid_" + os.urandom(6).hex(),
        },
        headers={"X-API-Key": raw},
    )
    assert r.status_code == 200

    before = await db.fetchval("SELECT COALESCE(MAX(id),0) FROM entity_changes")
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    rows = await _outbox_rows(db, person_id, before)
    assert rows and all(r["source_key_id"] is None for r in rows)
