"""#467: a merge must preserve assignment identity and tell the change feed.

Merging an org used to migrate the loser role's assignments by INSERT-a-copy +
DELETE-the-original, reminting every ULID, and hard-deleted the loser's roles and
assignments without a tombstone. A producer holding `pm_assignment_id` anchors saw
its rows 404 with nothing on `/api/v1/changes` to explain it.

The contract these tests pin:

1. a non-colliding assignment keeps its **id** across every merge path;
2. every role / role_assignment a merge hard-deletes gets a `deleted_entities`
   row carrying `merged_into` = the survivor, so the outbox emits a rebind signal;
3. subscriptions follow the entity instead of dangling.
"""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_id(db):
    """Insert an app_user + api_key; return the api_key_id."""
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "m467@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Merge Signals",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return kid


async def _org(db, name):
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


async def _role(db, org_id, title="Member"):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)", rid, org_id, title
    )
    return rid


async def _person(db, name="Merge Signals Person"):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _assign(db, person_id, role_id, start, source_key_id=None):
    """`start` is an ISO date string; asyncpg wants a real `date`."""
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, source_key_id)"
        " VALUES ($1, $2, $3, $4, $5)",
        aid,
        person_id,
        role_id,
        date.fromisoformat(start),
        source_key_id,
    )
    return aid


async def _tombstone(db, entity_type, entity_id):
    return await db.fetchrow(
        "SELECT merged_into FROM deleted_entities WHERE entity_type=$1 AND entity_id=$2",
        entity_type,
        entity_id,
    )


async def _subscribe(db, api_key_id, entity_type, entity_id):
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1, $2, $3)",
        api_key_id,
        entity_id,
        entity_type,
    )


# ---------------------------------------------------------------------------
# 1. Identity preservation — the incident itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("submit_pairs", [True, False], ids=["role_pairs", "safeguard"])
async def test_org_merge_repoints_non_colliding_assignment_keeping_its_id(
    client, db, api_key_id, submit_pairs
):
    """A loser assignment with no winner-side twin keeps its ULID and provenance.

    Both conflict-resolution blocks in `_execute_merge` are covered: the explicit
    `merge_role_pairs` one and the safeguard that catches unsubmitted conflicts.
    """
    win_org, lose_org = await _org(db, "Transportation Cmte"), await _org(db, "Cmte on Transp")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)
    person = await _person(db)
    # Winner-side row exists on the SAME role title but a different tenure, so the
    # role titles collide while the assignments do not.
    await _assign(db, await _person(db, "Sitting Member"), win_role, "2021-01-01")
    historical = await _assign(db, person, lose_role, "1999-01-01", source_key_id=api_key_id)

    data = {"merge_role_pairs": f"{win_role}:{lose_role}"} if submit_pairs else {}
    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        data=data,
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303

    row = await db.fetchrow(
        "SELECT role_id, source_key_id FROM role_assignments WHERE id=$1", historical
    )
    assert row is not None, "assignment id was reminted — producer anchors break"
    assert row["role_id"] == win_role
    assert row["source_key_id"] == api_key_id


async def test_org_merge_repoints_archived_assignment_keeping_its_id(client, db):
    """Archived loser assignments re-point too — a retracted tenure keeps its anchor."""
    win_org, lose_org = await _org(db, "Archived Win"), await _org(db, "Archived Lose")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)
    person = await _person(db)
    retracted = await _assign(db, person, lose_role, "2005-01-01")
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", retracted)

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        data={"merge_role_pairs": f"{win_role}:{lose_role}"},
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert await db.fetchval("SELECT role_id FROM role_assignments WHERE id=$1", retracted) == (
        win_role
    )


# ---------------------------------------------------------------------------
# 2. Tombstones — the notification gap
# ---------------------------------------------------------------------------


async def test_org_merge_tombstones_dropped_duplicate_assignment(client, db):
    """A genuine (person, role, start) collision is still dropped — but announced."""
    win_org, lose_org = await _org(db, "Dup Win"), await _org(db, "Dup Lose")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)
    person = await _person(db)
    survivor = await _assign(db, person, win_role, "2020-01-01")
    dropped = await _assign(db, person, lose_role, "2020-01-01")

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        data={"merge_role_pairs": f"{win_role}:{lose_role}"},
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert await db.fetchval("SELECT id FROM role_assignments WHERE id=$1", dropped) is None

    tomb = await _tombstone(db, "role_assignment", dropped)
    assert tomb is not None, "dropped assignment vanished with no tombstone"
    assert tomb["merged_into"] == survivor

    change = await db.fetchrow(
        "SELECT change_kind, merged_into FROM entity_changes"
        " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='deleted'",
        dropped,
    )
    assert change is not None, "no 'deleted' outbox row — a subscriber sees nothing"
    assert change["merged_into"] == survivor


async def test_org_merge_tombstones_emptied_loser_role(client, db):
    """The loser role is hard-deleted; the feed learns which role replaced it."""
    win_org, lose_org = await _org(db, "Role Tomb Win"), await _org(db, "Role Tomb Lose")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        data={"merge_role_pairs": f"{win_role}:{lose_role}"},
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert await db.fetchval("SELECT id FROM roles WHERE id=$1", lose_role) is None

    tomb = await _tombstone(db, "role", lose_role)
    assert tomb is not None, "loser role vanished with no tombstone"
    assert tomb["merged_into"] == win_role


async def test_role_merge_tombstones_and_preserves_ids(client, db):
    """The org-detail role-merge route owes the same signals (#467)."""
    org = await _org(db, "Role Merge Signals Org")
    # Same-org role merge is not title-scoped: two distinct titles collapse into one.
    win_role, lose_role = await _role(db, org, "Chair"), await _role(db, org, "Chairman")
    person = await _person(db)
    survivor = await _assign(db, person, win_role, "2020-01-01")
    dropped = await _assign(db, person, lose_role, "2020-01-01")
    kept = await _assign(db, await _person(db, "Other"), lose_role, "2018-01-01")

    response = await client.post(
        f"/admin/orgs/{org}/roles/{win_role}/merge/{lose_role}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert await db.fetchval("SELECT role_id FROM role_assignments WHERE id=$1", kept) == win_role

    assert (await _tombstone(db, "role_assignment", dropped))["merged_into"] == survivor
    assert (await _tombstone(db, "role", lose_role))["merged_into"] == win_role


async def test_person_merge_tombstones_dropped_duplicate_assignment(client, db):
    """Person merge drops (role, start) collisions — announce them too (#467)."""
    org = await _org(db, "Person Merge Signals Org")
    role = await _role(db, org)
    winner, loser = await _person(db, "Jane A Doe"), await _person(db, "Jane Doe")
    survivor = await _assign(db, winner, role, "2020-01-01")
    dropped = await _assign(db, loser, role, "2020-01-01")

    response = await client.post(
        f"/admin/people/{winner}/merge-with/{loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (await _tombstone(db, "role_assignment", dropped))["merged_into"] == survivor


# ---------------------------------------------------------------------------
# 3. Subscriptions follow the entity
# ---------------------------------------------------------------------------


async def test_org_merge_rehomes_org_subscription(client, db, api_key_id):
    """A subscription on the loser org follows it to the winner."""
    win_org, lose_org = await _org(db, "Sub Win"), await _org(db, "Sub Lose")
    await _subscribe(db, api_key_id, "organization", lose_org)

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        await db.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions"
            " WHERE api_key_id=$1 AND entity_id=$2 AND entity_type='organization'",
            api_key_id,
            win_org,
        )
        == 1
    )
    assert (
        await db.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions WHERE entity_id=$1", lose_org
        )
        == 0
    )


async def test_org_merge_rehomes_subscription_of_dropped_assignment(client, db, api_key_id):
    """A subscription anchored to a dropped duplicate follows to the survivor."""
    win_org, lose_org = await _org(db, "Sub RA Win"), await _org(db, "Sub RA Lose")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)
    person = await _person(db)
    survivor = await _assign(db, person, win_role, "2020-01-01")
    dropped = await _assign(db, person, lose_role, "2020-01-01")
    await _subscribe(db, api_key_id, "role_assignment", dropped)

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        data={"merge_role_pairs": f"{win_role}:{lose_role}"},
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        await db.fetchval(
            "SELECT entity_id FROM api_key_entity_subscriptions WHERE api_key_id=$1", api_key_id
        )
        == survivor
    )


async def test_merge_rehome_collapses_duplicate_subscription(client, db, api_key_id):
    """Subscribed to both sides: the pair collapses to one row, no PK violation."""
    win_org, lose_org = await _org(db, "Sub Both Win"), await _org(db, "Sub Both Lose")
    await _subscribe(db, api_key_id, "organization", win_org)
    await _subscribe(db, api_key_id, "organization", lose_org)

    response = await client.post(
        f"/admin/orgs/{win_org}/merge-with/{lose_org}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        await db.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions WHERE api_key_id=$1", api_key_id
        )
        == 1
    )


# ---------------------------------------------------------------------------
# 4. Blast radius is stated before the merge, not after
# ---------------------------------------------------------------------------


async def test_merge_preview_reports_assignment_blast_radius(client, db):
    """The preview modal states how many assignments move and how many collapse."""
    win_org, lose_org = await _org(db, "Preview Win"), await _org(db, "Preview Lose")
    win_role, lose_role = await _role(db, win_org), await _role(db, lose_org)
    shared = await _person(db, "Shared Member")
    await _assign(db, shared, win_role, "2020-01-01")
    await _assign(db, shared, lose_role, "2020-01-01")  # collides → dropped
    await _assign(db, await _person(db, "Historic A"), lose_role, "1999-01-01")
    await _assign(db, await _person(db, "Historic B"), lose_role, "2001-01-01")

    response = await client.get(
        f"/admin/orgs/{win_org}/merge-preview/{lose_org}/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "2 role assignments" in response.text
    assert "1 duplicate role assignment" in response.text
