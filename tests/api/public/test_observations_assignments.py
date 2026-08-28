"""Integration tests for POST /api/v1/assignments/observations + changes-feed coverage."""

import hashlib
import os
from datetime import date

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_BASE = "/api/v1/assignments/observations"
_CHANGES = "/api/v1/changes"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def obs_scope(db):
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id=$1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    return scope_id


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db, obs_scope):
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "asgn_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Asgn Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, obs_scope
    )
    return raw, kid


@pytest_asyncio.fixture(loop_scope="session")
async def write_key2(db, obs_scope):
    """A second observations:write key — for source_key_id authority tests (#311)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "asgn_obs2@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Asgn Obs Key 2",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, obs_scope
    )
    return raw, kid


@pytest_asyncio.fixture(loop_scope="session")
async def read_key(db):
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "asgn_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Asgn Read Key",
        raw[:8],
        key_hash,
    )
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def obs_entities(db):
    """Seed person + org + role for observation tests."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Test Role')",
        role_id,
        org_id,
    )
    return {"person_id": person_id, "role_id": role_id, "org_id": org_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _post(client, raw_key, payload):
    return await client.post(_BASE, json=payload, headers={"X-API-Key": raw_key})


def _base_payload(entities: dict) -> dict:
    return {
        "person_id": entities["person_id"],
        "role_id": entities["role_id"],
        "start_date": None,
    }


# ---------------------------------------------------------------------------
# Auth / scope
# ---------------------------------------------------------------------------


async def test_obs_requires_api_key(client):
    r = await client.post(_BASE, json={"person_id": "x", "role_id": "x"})
    assert r.status_code == 403


async def test_obs_requires_write_scope(client, read_key, obs_entities):
    r = await _post(client, read_key, _base_payload(obs_entities))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_returns_new_disposition(client, write_key, obs_entities):
    raw, _ = write_key
    r = await _post(client, raw, _base_payload(obs_entities))
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "role_assignment"


async def test_new_persisted(client, write_key, obs_entities, db):
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2024-03-01",
        "end_date": "2025-02-28",
        "is_current": False,
        "notes": "Test assignment notes",
    }
    r = await _post(client, raw, payload)
    assert r.status_code == 200
    aid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT person_id, role_id, start_date, end_date, is_current, notes"
        " FROM role_assignments WHERE id=$1",
        aid,
    )
    assert row["person_id"] == obs_entities["person_id"]
    assert row["role_id"] == obs_entities["role_id"]
    assert str(row["start_date"]) == "2024-03-01"
    assert str(row["end_date"]) == "2025-02-28"
    assert row["is_current"] is False
    assert row["notes"] == "Test assignment notes"


async def test_new_null_start_date(client, write_key, obs_entities, db):
    """NULL start_date is accepted and creates a NEW assignment (unknown start)."""
    raw, _ = write_key
    # Dedicated role so this test owns the NULL-start slot independently.
    role_null = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'NullDate Role')",
        role_null,
        obs_entities["org_id"],
    )
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_null,
            "start_date": None,
            "is_current": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "new"


# ---------------------------------------------------------------------------
# Disposition — AUTO_ATTACHED
# ---------------------------------------------------------------------------


async def test_auto_attached_on_same_natural_key(client, write_key, obs_entities):
    """Same (person_id, role_id, start_date) → AUTO_ATTACHED, no duplicate row."""
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2025-01-01",
    }
    r1 = await _post(client, raw, payload)
    assert r1.json()["disposition"] == "new"
    aid = r1.json()["entity_id"]

    r2 = await _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == aid
    # #477: a healthy attach to a *live* row carries no archived signal. The field
    # is optional-absent rather than false so the common case stays quiet.
    assert r2.json()["attached_archived"] is None


async def test_auto_attached_null_start_date_dedup(client, write_key, obs_entities, db):
    """Two NULL start_date submissions → second is AUTO_ATTACHED (NULLS NOT DISTINCT)."""
    raw, _ = write_key

    # Need a distinct role so this test doesn't collide with test_new_null_start_date.
    org_id = obs_entities["org_id"]
    role_b = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'NullStart Role')",
        role_b,
        org_id,
    )

    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": role_b,
        "start_date": None,
    }
    r1 = await _post(client, raw, payload)
    assert r1.json()["disposition"] == "new"
    aid = r1.json()["entity_id"]

    r2 = await _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == aid


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_on_unknown_person(client, write_key, obs_entities):
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {"person_id": generate_id(), "role_id": obs_entities["role_id"]},
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_on_unknown_role(client, write_key, obs_entities):
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {"person_id": obs_entities["person_id"], "role_id": generate_id()},
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


async def test_is_current_with_end_date_rejected(client, write_key, obs_entities):
    """is_current=True + end_date set → 422 from model validator."""
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "is_current": True,
            "end_date": "2025-12-31",
        },
    )
    assert r.status_code == 422


async def test_date_order_validation(client, write_key, obs_entities):
    """start_date after end_date → 422."""
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2025-06-01",
            "end_date": "2024-01-01",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Attribute writes — links
# ---------------------------------------------------------------------------


async def test_link_written_on_new(client, write_key, obs_entities, link_type, db):
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2025-07-01",
            "links": [{"url": "https://example.com/asgn", "link_type_slug": "website"}],
        },
    )
    assert r.json()["disposition"] == "new"
    aid = r.json()["entity_id"]
    link = await db.fetchrow(
        "SELECT url FROM links WHERE entity_type='role_assignment' AND entity_id=$1", aid
    )
    assert link["url"] == "https://example.com/asgn"


async def test_link_deduped_on_auto_attached(client, write_key, obs_entities, db):
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2025-08-01",
        "links": [{"url": "https://example.com/dup-asgn", "link_type_slug": "website"}],
    }
    await _post(client, raw, payload)
    await _post(client, raw, payload)
    count = await db.fetchval(
        "SELECT COUNT(*) FROM links l"
        " JOIN role_assignments ra ON ra.id=l.entity_id"
        " WHERE l.entity_type='role_assignment' AND ra.start_date='2025-08-01'"
        "   AND ra.person_id=$1 AND l.url='https://example.com/dup-asgn'",
        obs_entities["person_id"],
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Attribute writes — contact_methods
# ---------------------------------------------------------------------------


async def test_contact_method_written(client, write_key, obs_entities, db):
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2025-09-01",
            "contact_methods": [{"contact_type": "email", "value": "asgn@example.com"}],
        },
    )
    assert r.json()["disposition"] == "new"
    aid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT value FROM contact_methods WHERE entity_type='role_assignment' AND entity_id=$1",
        aid,
    )
    assert row is not None


# ---------------------------------------------------------------------------
# Attribute writes — addresses
# ---------------------------------------------------------------------------


async def test_address_written(client, write_key, obs_entities, db, local_address_normalizer):
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2025-10-01",
            "addresses": [{"raw_input": "999 Assignment Blvd"}],
        },
    )
    assert r.json()["disposition"] == "new"
    aid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT ea.id FROM entity_addresses ea"
        " WHERE ea.entity_type='role_assignment' AND ea.entity_id=$1",
        aid,
    )
    assert row is not None


# ---------------------------------------------------------------------------
# Changes feed — role_assignment coverage
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def changes_api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "asgn_changes@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Asgn Changes Key",
        raw_key[:8],
        key_hash,
    )
    return {"raw_key": raw_key, "key_id": kid}


@pytest_asyncio.fixture(loop_scope="session")
async def asgn_change_fixtures(db, changes_api_key):
    before_seq = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    asgn_id = generate_id()
    kid = changes_api_key["key_id"]
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Feed Role')",
        role_id,
        org_id,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        asgn_id,
        person_id,
        role_id,
    )
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'role_assignment')",
        kid,
        asgn_id,
    )
    return {"before_seq": before_seq, "asgn_id": asgn_id}


async def test_changes_includes_role_assignments(client, changes_api_key, asgn_change_fixtures):
    r = await client.get(
        _CHANGES,
        params={"after": asgn_change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": changes_api_key["raw_key"]},
    )
    assert r.status_code == 200
    items = r.json()["data"]
    asgn_items = [i for i in items if i["entity_id"] == asgn_change_fixtures["asgn_id"]]
    assert len(asgn_items) >= 1
    assert asgn_items[0]["entity_type"] == "role_assignment"
    assert asgn_items[0]["change_kind"] == "updated"


# ---------------------------------------------------------------------------
# PM-native identifier (#198)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def pm_target_assignment(db):
    """Isolated org/person/role/assignment for pm_assignment_id tests."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    asgn_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        org_id,
        "PM Test Role",
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        asgn_id,
        person_id,
        role_id,
    )
    return asgn_id


async def test_pm_assignment_id_auto_attached(client, write_key, pm_target_assignment):
    """pm_assignment_id targets an existing assignment by PM ULID → auto-attached."""
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": pm_target_assignment,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == pm_target_assignment
    assert body["entity_type"] == "role_assignment"


async def test_pm_assignment_id_rejected_on_unknown_ulid(client, write_key):
    """identifier_type=pm_assignment_id with unknown ULID → rejected."""
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": generate_id(),
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_pm_assignment_id_requires_identifier_value(client, write_key):
    """identifier_type=pm_assignment_id without identifier_value → 422."""
    raw, _ = write_key
    r = await _post(client, raw, {"identifier_type": "pm_assignment_id"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# #289 — backfill start_date onto an undated tenure via pm_assignment_id
# (NULL → dated promotion, out of band from match-or-create)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def undated_assignment(db):
    """Fresh isolated undated (NULL start_date) assignment per test."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    asgn_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        org_id,
        "Backfill Test Role",
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        asgn_id,
        person_id,
        role_id,
    )
    yield {"asgn_id": asgn_id, "person_id": person_id, "role_id": role_id}
    await db.execute("DELETE FROM role_assignments WHERE person_id=$1", person_id)
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


async def test_pm_assignment_id_backfills_null_start_date(
    client, write_key, undated_assignment, db
):
    """pm_assignment_id + start_date sets the date on an undated row — same row, no new row."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    person_id = undated_assignment["person_id"]

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2013-01-14",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == asgn_id

    rows = await db.fetch(
        "SELECT id, start_date FROM role_assignments WHERE person_id=$1", person_id
    )
    assert len(rows) == 1  # promoted in place, not duplicated
    assert str(rows[0]["start_date"]) == "2013-01-14"


async def test_pm_assignment_id_backfills_end_date(client, write_key, undated_assignment, db):
    """pm_assignment_id + end_date closes an open tenure in place (#289)."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2013-01-14",
            "end_date": "2019-01-13",
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow(
        "SELECT start_date, end_date FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["start_date"]) == "2013-01-14"
    assert str(row["end_date"]) == "2019-01-13"


async def test_pm_update_closes_current_tenure(client, write_key, obs_entities, db):
    """#311 (supersedes #289 check-violation reject): a dated end closes a current tenure.

    A supplied end_date with is_current omitted implies the tenure has ended —
    is_current flips to FALSE in the same update (chk_current_no_end_date holds).
    """
    raw, _ = write_key
    asgn_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        asgn_id,
        obs_entities["person_id"],
        obs_entities["role_id"],
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2020-01-01",
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["end_date"]) == "2020-01-01"
    assert row["is_current"] is False


async def test_new_create_rolls_back_when_side_data_rejected(client, write_key, obs_entities, db):
    """#289 dir.6: a bad link in the same payload rolls back the NEW assignment too."""
    raw, _ = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2022-06-01",
            "links": [{"url": "https://x.example", "link_type_slug": "___nonexistent___"}],
        },
    )
    assert r.json()["disposition"] == "rejected"
    rows = await db.fetch(
        "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        obs_entities["person_id"],
        obs_entities["role_id"],
    )
    assert rows == []  # create rolled back with the side-data failure


async def test_pm_update_overwrites_end_date(client, write_key, undated_assignment, db):
    """#311 (supersedes #289 conflict-reject): a different end_date updates in place."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2019-01-13",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2020-01-01",
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow("SELECT end_date FROM role_assignments WHERE id=$1", asgn_id)
    assert str(row["end_date"]) == "2020-01-01"  # extended in place


async def test_pm_assignment_id_backfill_idempotent(client, write_key, undated_assignment, db):
    """Re-sending the same start_date via pm_assignment_id → auto-attached, unchanged."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    payload = {
        "identifier_type": "pm_assignment_id",
        "identifier_value": asgn_id,
        "start_date": "2013-01-14",
    }
    assert (await _post(client, raw, payload)).json()["disposition"] == "auto-attached"
    r2 = await _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", asgn_id)
    assert str(row["start_date"]) == "2013-01-14"


async def test_pm_update_moves_start_date(client, write_key, undated_assignment, db):
    """#311 (supersedes #289 conflict-reject): a different start_date moves in place.

    The issue's concrete case: a producer's merged-span backfill deepens a
    tenure's start (2019 → 2017). Same row, no duplicate minted.
    """
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    person_id = undated_assignment["person_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2019-01-01",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2017-01-01",
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "auto-attached"
    rows = await db.fetch(
        "SELECT id, start_date FROM role_assignments WHERE person_id=$1", person_id
    )
    assert len(rows) == 1  # moved in place, not duplicated
    assert str(rows[0]["start_date"]) == "2017-01-01"


async def test_pm_assignment_id_backfill_sibling_collision_rejected(
    client, write_key, obs_entities, db
):
    """Backfilling an undated row onto a date a sibling tenure already holds → rejected."""
    raw, _ = write_key
    base = {"person_id": obs_entities["person_id"], "role_id": obs_entities["role_id"]}

    undated_id = (await _post(client, raw, {**base, "start_date": None})).json()["entity_id"]
    await _post(client, raw, {**base, "start_date": "2013-01-14"})  # sibling occupies the date

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": undated_id,
            "start_date": "2013-01-14",
        },
    )
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "start_date_conflict"
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", undated_id)
    assert row["start_date"] is None  # undated row untouched


async def test_returning_legislator_tenures_coexist(client, write_key, obs_entities, db):
    """#289 regression: undated + two dated tenures for one (person, role) coexist as 3 rows."""
    raw, _ = write_key
    person_id = obs_entities["person_id"]
    base = {"person_id": person_id, "role_id": obs_entities["role_id"]}

    r_undated = await _post(client, raw, {**base, "start_date": None})
    r_first = await _post(client, raw, {**base, "start_date": "2013-01-14"})
    r_second = await _post(client, raw, {**base, "start_date": "2021-01-11"})

    ids = {r.json()["entity_id"] for r in (r_undated, r_first, r_second)}
    assert all(r.json()["disposition"] == "new" for r in (r_undated, r_first, r_second))
    assert len(ids) == 3  # three distinct assignment rows

    rows = await db.fetch(
        "SELECT start_date FROM role_assignments WHERE person_id=$1 AND role_id=$2"
        " AND archived_at IS NULL",
        person_id,
        obs_entities["role_id"],
    )
    starts = {str(r["start_date"]) for r in rows}
    assert starts == {"None", "2013-01-14", "2021-01-11"}


# ---------------------------------------------------------------------------
# #225 — reason field on rejected observations
# ---------------------------------------------------------------------------


async def test_rejected_unknown_person_includes_reason(client, write_key, obs_entities):
    """Unknown person_id rejection must include a reason string."""
    raw, _ = write_key
    unknown_person_id = generate_id()
    r = await _post(
        client,
        raw,
        {"person_id": unknown_person_id, "role_id": obs_entities["role_id"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None
    assert "person_not_found" in body["reason"]


# ---------------------------------------------------------------------------
# #311 — source_key_id provenance + authoritative pm-native updates
# ---------------------------------------------------------------------------


async def test_new_stamps_source_key_id(client, write_key, obs_entities, db):
    """A NEW assignment records the observing key as its source (#311)."""
    raw, kid = write_key
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2026-01-01",
        },
    )
    assert r.json()["disposition"] == "new"
    row = await db.fetchrow(
        "SELECT source_key_id FROM role_assignments WHERE id=$1", r.json()["entity_id"]
    )
    assert row["source_key_id"] == kid


async def test_pm_update_reopens_tenure(client, write_key, undated_assignment, db):
    """Explicit end_date=null + is_current=true reopens a closed tenure (#311).

    The issue's Caldier case: a sitting legislator wrongly shown as ended.
    JSON null must clear the bound — distinct from omitting the field.
    """
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2024-12-31",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": None,
            "is_current": True,
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert row["end_date"] is None
    assert row["is_current"] is True


async def test_pm_update_omitted_end_date_unchanged(client, write_key, undated_assignment, db):
    """An omitted end_date leaves the stored bound alone — only explicit null clears."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2013-01-14",
            "end_date": "2019-01-13",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2011-01-10",
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow(
        "SELECT start_date, end_date FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["start_date"]) == "2011-01-10"
    assert str(row["end_date"]) == "2019-01-13"  # untouched


async def test_pm_update_source_key_mismatch_rejected(
    client, write_key, write_key2, obs_entities, db
):
    """A key must not update an assignment sourced by another key (#311)."""
    raw_a, _ = write_key
    raw_b, _ = write_key2
    r_new = await _post(
        client,
        raw_a,
        {
            "person_id": obs_entities["person_id"],
            "role_id": obs_entities["role_id"],
            "start_date": "2015-01-01",
        },
    )
    asgn_id = r_new.json()["entity_id"]
    r = await _post(
        client,
        raw_b,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2020-01-01",
        },
    )
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "source_key_mismatch"
    row = await db.fetchrow("SELECT end_date FROM role_assignments WHERE id=$1", asgn_id)
    assert row["end_date"] is None  # untouched


async def test_pm_update_claims_null_source(client, write_key, undated_assignment, db):
    """Updating a pre-#311 (NULL source) row is allowed and claims provenance."""
    raw, kid = write_key
    asgn_id = undated_assignment["asgn_id"]
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2013-01-14",
        },
    )
    assert r.json()["disposition"] == "auto-attached"
    row = await db.fetchrow("SELECT source_key_id FROM role_assignments WHERE id=$1", asgn_id)
    assert row["source_key_id"] == kid


async def test_pm_update_is_current_with_stored_end_rejected(
    client, write_key, undated_assignment, db
):
    """is_current=true with a stored end_date left in place → rejected (send end_date: null)."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "end_date": "2024-12-31",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "is_current": True,
        },
    )
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "is_current_end_date_conflict"
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["end_date"]) == "2024-12-31"
    assert row["is_current"] is False


async def test_pm_update_start_after_end_rejected(client, write_key, undated_assignment, db):
    """Moving start past the stored end → rejected, row untouched."""
    raw, _ = write_key
    asgn_id = undated_assignment["asgn_id"]
    await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2013-01-14",
            "end_date": "2019-01-13",
        },
    )
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": asgn_id,
            "start_date": "2020-05-01",
        },
    )
    assert r.json()["disposition"] == "rejected"
    assert r.json()["reason"] == "start_after_end_date"
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", asgn_id)
    assert str(row["start_date"]) == "2013-01-14"  # unchanged


# ---------------------------------------------------------------------------
# #311 — natural-key auto-attach: close-enrichment + `unapplied` signaling
# ---------------------------------------------------------------------------


async def test_attach_closes_open_tenure(client, write_key, obs_entities, db):
    """Auto-attach applies a dated end to an open tenure (the election-cycle close).

    The issue's committee case: stored end NULL + is_current TRUE; observation
    carries end + is_current=false → applied in place, nothing unapplied.
    """
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2023-01-09",
    }
    r_new = await _post(client, raw, {**payload, "is_current": True})
    assert r_new.json()["disposition"] == "new"
    asgn_id = r_new.json()["entity_id"]

    r = await _post(client, raw, {**payload, "end_date": "2025-01-01", "is_current": False})
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == asgn_id
    assert body.get("unapplied") in (None, [])
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["end_date"]) == "2025-01-01"
    assert row["is_current"] is False


async def test_attach_conflicting_end_unapplied(client, write_key, obs_entities, db):
    """Auto-attach never overwrites a differing non-NULL end — reports it unapplied."""
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2013-01-14",
    }
    r_new = await _post(client, raw, {**payload, "end_date": "2016-12-31"})
    asgn_id = r_new.json()["entity_id"]

    r = await _post(client, raw, {**payload, "end_date": "2024-12-31"})
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["unapplied"] == ["end_date"]
    row = await db.fetchrow("SELECT end_date FROM role_assignments WHERE id=$1", asgn_id)
    assert str(row["end_date"]) == "2016-12-31"  # unchanged


async def test_attach_is_current_flip_unapplied(client, write_key, obs_entities, db):
    """Auto-attach never flips is_current on its own — reports it unapplied."""
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2021-01-11",
    }
    r_new = await _post(client, raw, {**payload, "end_date": "2024-12-31"})
    asgn_id = r_new.json()["entity_id"]

    r = await _post(client, raw, {**payload, "is_current": True})
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["unapplied"] == ["is_current"]
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert str(row["end_date"]) == "2024-12-31"
    assert row["is_current"] is False


async def test_attach_close_blocked_for_other_source(
    client, write_key, write_key2, obs_entities, db
):
    """Close-enrichment respects provenance: another key's row is not mutated."""
    raw_a, _ = write_key
    raw_b, _ = write_key2
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2019-01-14",
    }
    r_new = await _post(client, raw_a, {**payload, "is_current": True})
    asgn_id = r_new.json()["entity_id"]

    r = await _post(client, raw_b, {**payload, "end_date": "2025-01-01", "is_current": False})
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["unapplied"] == ["end_date", "is_current"]
    row = await db.fetchrow(
        "SELECT end_date, is_current FROM role_assignments WHERE id=$1", asgn_id
    )
    assert row["end_date"] is None  # untouched
    assert row["is_current"] is True


async def test_attach_matching_values_nothing_unapplied(client, write_key, obs_entities, db):
    """Re-observing identical bounds is clean — no unapplied noise."""
    raw, _ = write_key
    payload = {
        "person_id": obs_entities["person_id"],
        "role_id": obs_entities["role_id"],
        "start_date": "2017-01-09",
        "end_date": "2019-01-13",
    }
    await _post(client, raw, payload)
    r = await _post(client, raw, payload)
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body.get("unapplied") in (None, [])


# ---------------------------------------------------------------------------
# Retraction — op="retract" (#391)
# ---------------------------------------------------------------------------


async def _outbox_count(db, assignment_id):
    """entity_changes rows for an assignment — the BIGSERIAL outbox is a true
    in-transaction observable (unlike now(), which is transaction-constant under
    the rollback client, so updated_at can't distinguish a bump from a no-op)."""
    return await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_id=$1 AND entity_type='role_assignment'",
        assignment_id,
    )


async def _seed_assignment(client, raw, db, obs_entities, start_date, **extra):
    """Create an assignment on a dedicated role so the test owns its identity slot."""
    role_id = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        obs_entities["org_id"],
        f"Retract Role {role_id[:8]}",
    )
    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": start_date,
            **extra,
        },
    )
    assert r.json()["disposition"] == "new", r.text
    return r.json()["entity_id"], role_id


def _retract(assignment_id, **extra):
    return {
        "identifier_type": "pm_assignment_id",
        "identifier_value": assignment_id,
        "op": "retract",
        **extra,
    }


async def test_retract_archives_assignment(client, write_key, obs_entities, db):
    """op=retract on a pm-native observation archives the tenure (never hard-delete)."""
    raw, _ = write_key
    aid, _ = await _seed_assignment(client, raw, db, obs_entities, "2001-01-08")

    before = await _outbox_count(db, aid)
    r = await _post(client, raw, _retract(aid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "retracted"
    assert body["entity_id"] == aid
    assert body["entity_type"] == "role_assignment"

    archived = await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", aid)
    assert archived is not None
    # the archive is an UPDATE → trg_entity_changes_role_assignments → outbox row
    # (subscribers mirror archived_at and drop the anchor)
    assert await _outbox_count(db, aid) > before


async def test_retract_re_emit_is_noop_no_outbox(client, write_key, obs_entities, db):
    """Re-retracting an already-archived assignment → auto-attached, no new outbox row.

    A stateful producer keeps sending the retract every cycle; the second one must
    skip the UPDATE (no producer<->PM ping-pong) — proven by a steady outbox count.
    """
    raw, _ = write_key
    aid, _ = await _seed_assignment(client, raw, db, obs_entities, "2002-01-14")

    r1 = await _post(client, raw, _retract(aid))
    assert r1.json()["disposition"] == "retracted"
    after_first = await _outbox_count(db, aid)

    r2 = await _post(client, raw, _retract(aid))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == aid
    # #477: the re-emitted retract is answered "auto-attached" too. Without the
    # flag a producer cannot tell that from a fresh attach to a live row.
    assert body["attached_archived"] is True
    assert await _outbox_count(db, aid) == after_first  # no-op, no emit


async def test_retract_without_pm_assignment_id_invalid(client, write_key, obs_entities, db):
    """Retract is always id-addressed; a natural-key payload → rejected/invalid."""
    raw, _ = write_key
    aid, role_id = await _seed_assignment(client, raw, db, obs_entities, "2003-01-13")

    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": "2003-01-13",
            "op": "retract",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "invalid"
    archived = await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", aid)
    assert archived is None


async def test_retract_unknown_id_assignment_not_found(client, write_key):
    raw, _ = write_key
    r = await _post(client, raw, _retract("01NONEXISTENTASGN000000000"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "assignment_not_found"


async def test_retract_foreign_source_key_mismatch(client, write_key, write_key2, obs_entities, db):
    """A different key retracting a source-stamped assignment → source_key_mismatch."""
    raw_a, _ = write_key
    raw_b, _ = write_key2
    aid, _ = await _seed_assignment(client, raw_a, db, obs_entities, "2004-01-12")

    r = await _post(client, raw_b, _retract(aid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "source_key_mismatch"
    archived = await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", aid)
    assert archived is None


async def test_retract_identity_mismatch_immutable(client, write_key, obs_entities, db):
    """A retract naming a person_id that isn't the stored one → identity_immutable.

    Guards a copy-paste pm_assignment_id that points at a different tenure.
    """
    raw, _ = write_key
    aid, _ = await _seed_assignment(client, raw, db, obs_entities, "2005-01-10")
    other_person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other_person)

    r = await _post(client, raw, _retract(aid, person_id=other_person))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "identity_immutable"
    archived = await db.fetchval("SELECT archived_at FROM role_assignments WHERE id=$1", aid)
    assert archived is None


async def test_retract_ignores_payload(client, write_key, obs_entities, db):
    """Retract ignores the refine payload — bounds untouched, no ancillary written."""
    raw, _ = write_key
    aid, _ = await _seed_assignment(
        client, raw, db, obs_entities, "2006-01-09", end_date="2008-01-13"
    )

    r = await _post(
        client,
        raw,
        _retract(
            aid,
            start_date="1999-01-01",
            end_date=None,
            is_current=True,
            links=[{"url": "https://example.org/retract", "link_type_slug": "website"}],
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["disposition"] == "retracted"

    row = await db.fetchrow(
        "SELECT start_date, end_date, is_current, archived_at FROM role_assignments WHERE id=$1",
        aid,
    )
    assert str(row["start_date"]) == "2006-01-09"  # unmoved
    assert str(row["end_date"]) == "2008-01-13"  # not cleared
    assert row["is_current"] is False  # not flipped
    assert row["archived_at"] is not None
    links = await db.fetchval(
        "SELECT COUNT(*) FROM links WHERE entity_type='role_assignment' AND entity_id=$1", aid
    )
    assert links == 0


async def test_reobserve_after_retract_stays_retracted(client, write_key, obs_entities, db):
    """Anti-resurrection: a natural-key re-observe of retracted content does NOT
    mint a fresh active row — it dedups against the archived twin (auto-attached),
    mirroring events #322 / citations #319 / relationships #301. A retract is
    authoritative; un-retract is a deliberate admin unarchive only.
    """
    raw, _ = write_key
    aid, role_id = await _seed_assignment(client, raw, db, obs_entities, "2007-01-08")
    r1 = await _post(client, raw, _retract(aid))
    assert r1.json()["disposition"] == "retracted"

    r2 = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": "2007-01-08",
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == aid  # the archived row, not a new one
    # #477: the whole point of the signal — this response is otherwise
    # indistinguishable from a healthy attach where one field didn't apply, which
    # is exactly what left usa-wa anchored to a dead row for a month (#474).
    assert body["attached_archived"] is True

    rows = await db.fetch(
        "SELECT id, archived_at FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        obs_entities["person_id"],
        role_id,
    )
    assert len(rows) == 1  # re-observation minted nothing
    assert rows[0]["archived_at"] is not None  # and the retract stuck


async def test_retract_cascades_relationship_edges(client, write_key, obs_entities, db):
    """Retracting a seat archives its dependent staff_of edges via the #301
    cascade trigger (WHEN-gated on archived_at) — no orphaned edges left behind."""
    raw, _ = write_key
    principal_id, _ = await _seed_assignment(client, raw, db, obs_entities, "2009-01-12")
    staffer_person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", staffer_person)
    staffer_role = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Retract Staffer')",
        staffer_role,
        obs_entities["org_id"],
    )
    staffer_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
        " VALUES ($1,$2,$3,'2009-01-12')",
        staffer_id,
        staffer_person,
        staffer_role,
    )
    rel_type_id = await db.fetchval(
        "SELECT id FROM role_assignment_relationship_types WHERE slug='staff_of'"
    )
    edge_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignment_relationships"
        " (id, from_assignment_id, to_assignment_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        edge_id,
        staffer_id,
        principal_id,
        rel_type_id,
    )

    r = await _post(client, raw, _retract(principal_id))
    assert r.json()["disposition"] == "retracted"

    edge_archived = await db.fetchval(
        "SELECT archived_at FROM role_assignment_relationships WHERE id=$1", edge_id
    )
    assert edge_archived is not None


async def test_reobserve_after_retract_skips_ancillary(
    client, write_key, obs_entities, db, local_address_normalizer
):
    """#391 CR1: the anti-resurrection attach must not write the rest of the
    payload onto the retracted row. Ancillary on a soft-deleted assignment is
    meaningless and each new row fires the #327 touch trigger — an outbox emit
    for an entity subscribers have already dropped. Withheld names come back in
    `unapplied` so the producer can stop retrying (the #311 honest-signaling rule).
    """
    raw, _ = write_key
    aid, role_id = await _seed_assignment(client, raw, db, obs_entities, "2010-01-11")
    assert (await _post(client, raw, _retract(aid))).json()["disposition"] == "retracted"

    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": "2010-01-11",
            "links": [{"url": "https://example.org/ghost", "link_type_slug": "website"}],
            "contact_methods": [{"contact_type": "email", "value": "ghost@example.com"}],
            "addresses": [{"raw_input": "1 Ghost Way"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == aid
    assert set(body["unapplied"] or []) == {"links", "contact_methods", "addresses"}

    for label, sql in (
        (
            "links",
            "SELECT COUNT(*) FROM links WHERE entity_type='role_assignment' AND entity_id=$1",
        ),
        (
            "contact_methods",
            "SELECT COUNT(*) FROM contact_methods"
            " WHERE entity_type='role_assignment' AND entity_id=$1",
        ),
        (
            "entity_addresses",
            "SELECT COUNT(*) FROM entity_addresses"
            " WHERE entity_type='role_assignment' AND entity_id=$1",
        ),
    ):
        assert await db.fetchval(sql, aid) == 0, f"{label} written onto a retracted assignment"


async def test_reobserve_after_retract_reports_unapplied_bounds(
    client, write_key, obs_entities, db
):
    """#391 CR1: a bound delta supplied against a retracted twin is withheld and
    echoed — not silently swallowed into a response identical to a clean attach."""
    raw, _ = write_key
    aid, role_id = await _seed_assignment(client, raw, db, obs_entities, "2011-01-10")
    assert (await _post(client, raw, _retract(aid))).json()["disposition"] == "retracted"

    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": "2011-01-10",
            "end_date": "2013-01-14",
        },
    )
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["unapplied"] == ["end_date"]
    row = await db.fetchrow("SELECT end_date, archived_at FROM role_assignments WHERE id=$1", aid)
    assert row["end_date"] is None  # never applied to a retracted row
    assert row["archived_at"] is not None


async def test_reobserve_after_retract_reports_matching_claim_unapplied(
    client, write_key, obs_entities, db
):
    """#391 CR2: a claim that *equals* the archived row's stored value is still
    withheld — and must still be reported.

    On an ACTIVE row "equals stored" means the claim is already true in PM. On a
    RETRACTED row PM asserts the tenure never existed, so the same claim is
    contradicted, not satisfied. Reusing the active-path equality test here left
    the likeliest payload silent: a producer re-emitting a currently-held tenure
    sends is_current=true, which is exactly what the row stored when retracted.
    """
    raw, _ = write_key
    aid, role_id = await _seed_assignment(
        client, raw, db, obs_entities, "2012-01-09", is_current=True
    )
    assert (await _post(client, raw, _retract(aid))).json()["disposition"] == "retracted"
    stored = await db.fetchrow("SELECT is_current FROM role_assignments WHERE id=$1", aid)
    assert stored["is_current"] is True  # the claim below matches stored exactly

    r = await _post(
        client,
        raw,
        {
            "person_id": obs_entities["person_id"],
            "role_id": role_id,
            "start_date": "2012-01-09",
            "is_current": True,
        },
    )
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["unapplied"] == ["is_current"]


# ---------------------------------------------------------------------------
# #478 - provenance claimed by an identical id-addressed observation
# ---------------------------------------------------------------------------


async def test_pm_native_identical_claims_unowned_row_and_says_so(
    client, write_key, pm_target_assignment, db
):
    """The #478 case end to end: agree with an unowned row, claim it, be told.

    6,698 active assignments predate #311 and carry ``source_key_id IS NULL``.
    Their spans are already correct, so before #478 the only way to claim one
    was to falsify a date - and the response looked like an ordinary attach.
    """
    raw, kid = write_key
    await db.execute(
        "UPDATE role_assignments SET start_date=$2, is_current=TRUE, source_key_id=NULL"
        " WHERE id=$1",
        pm_target_assignment,
        date(2013, 1, 14),
    )

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": pm_target_assignment,
            "start_date": "2013-01-14",
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["provenance_claimed"] is True
    row = await db.fetchrow(
        "SELECT start_date, source_key_id FROM role_assignments WHERE id=$1",
        pm_target_assignment,
    )
    assert row["source_key_id"] == kid
    assert row["start_date"] == date(2013, 1, 14)  # nothing else moved


async def test_pm_native_identical_on_own_row_reports_no_claim(
    client, write_key, pm_target_assignment, db
):
    """Second delivery: already ours, so the field goes quiet again (#478)."""
    raw, kid = write_key
    await db.execute(
        "UPDATE role_assignments SET start_date=$2, is_current=TRUE, source_key_id=$3 WHERE id=$1",
        pm_target_assignment,
        date(2013, 1, 14),
        kid,
    )

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": pm_target_assignment,
            "start_date": "2013-01-14",
        },
    )

    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["provenance_claimed"] is None


async def test_pm_native_identical_foreign_row_claims_nothing(
    client, write_key, write_key2, pm_target_assignment, db
):
    """CR round 1 (#311) intact: a foreign key gains nothing by agreeing."""
    raw, _ = write_key
    _, other_kid = write_key2
    await db.execute(
        "UPDATE role_assignments SET start_date=$2, is_current=TRUE, source_key_id=$3 WHERE id=$1",
        pm_target_assignment,
        date(2013, 1, 14),
        other_kid,
    )

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": pm_target_assignment,
            "start_date": "2013-01-14",
        },
    )

    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["provenance_claimed"] is None
    owner = await db.fetchval(
        "SELECT source_key_id FROM role_assignments WHERE id=$1", pm_target_assignment
    )
    assert owner == other_kid


async def test_pm_native_differing_foreign_row_still_rejects(
    client, write_key, write_key2, pm_target_assignment, db
):
    """Disagreement with a foreign-owned row is still ``source_key_mismatch``."""
    raw, _ = write_key
    _, other_kid = write_key2
    await db.execute(
        "UPDATE role_assignments SET start_date=$2, is_current=TRUE, source_key_id=$3 WHERE id=$1",
        pm_target_assignment,
        date(2013, 1, 14),
        other_kid,
    )

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": pm_target_assignment,
            "start_date": "2014-01-01",
        },
    )

    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "source_key_mismatch"
    assert body["provenance_claimed"] is None
