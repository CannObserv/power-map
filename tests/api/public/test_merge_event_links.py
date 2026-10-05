"""An admin org merge re-points other orgs' succession links to the survivor (#611).

Crosses the seam from the admin merge route to the public org detail: a
predecessor whose ``succeeded_by`` event named the merged-away org must read the
survivor afterwards, with a new ETag and a change-feed row, so a consumer
holding the predecessor revalidates. Before #611 the merge left the link naming
a deleted id.

Runs on the committing client: the ETag derives from ``updated_at``, which a
single rolled-back transaction freezes at its start time.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

ADMIN_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(committing_db):
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await committing_db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid.lower()}@m611.test"
    )
    await committing_db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Merge Event Links",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return raw_key


async def _org(db, name) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def test_org_merge_repoints_predecessor_link_through_public_api(
    committing_client, committing_db, api_key
):
    db, client = committing_db, committing_client
    pred = await _org(db, "M611 Predecessor Committee")
    loser = await _org(db, "M611 Successor Committee")
    winner = await _org(db, "M611 Successor Committee (dup)")
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)
           SELECT $1, 'organization', $2, t.id, 'organization', $3
             FROM entity_event_types t WHERE t.slug = 'succeeded_by'""",
        generate_id(),
        pred,
        loser,
    )
    headers = {"X-API-Key": api_key}
    before = await client.get(f"/api/v1/orgs/{pred}", headers=headers)
    assert before.status_code == 200
    assert before.json()["succeeded_by"] == loser
    last_change = await db.fetchval("SELECT coalesce(max(id), 0) FROM entity_changes")

    merged = await client.post(f"/admin/orgs/{winner}/merge/{loser}/", headers=ADMIN_HEADERS)
    assert merged.status_code == 200

    after = await client.get(f"/api/v1/orgs/{pred}", headers=headers)
    assert after.status_code == 200
    assert after.json()["succeeded_by"] == winner
    assert after.headers["etag"] != before.headers["etag"]
    assert await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE id > $1 AND entity_type='organization' AND entity_id=$2",
        last_change,
        pred,
    )
    successor = await client.get(f"/api/v1/orgs/{winner}", headers=headers)
    assert successor.json()["succeeds"] == pred
    assert not await db.fetchval(
        "SELECT count(*) FROM entity_events WHERE entity_id=$1 OR linked_entity_id=$1", loser
    )
