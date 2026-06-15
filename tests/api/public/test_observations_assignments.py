"""Integration tests for POST /api/v1/assignments/observations + changes-feed coverage."""

import hashlib
import os

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
    yield scope_id


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
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


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
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


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
    yield {"person_id": person_id, "role_id": role_id, "org_id": org_id}
    await db.execute("DELETE FROM role_assignments WHERE person_id=$1", person_id)
    await db.execute("DELETE FROM role_assignments WHERE role_id=$1", role_id)
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(client, raw_key, payload):
    return client.post(_BASE, json=payload, headers={"X-API-Key": raw_key})


def _base_payload(entities: dict) -> dict:
    return {
        "person_id": entities["person_id"],
        "role_id": entities["role_id"],
        "start_date": None,
    }


# ---------------------------------------------------------------------------
# Auth / scope
# ---------------------------------------------------------------------------


def test_obs_requires_api_key(client):
    r = client.post(_BASE, json={"person_id": "x", "role_id": "x"})
    assert r.status_code == 403


async def test_obs_requires_write_scope(client, read_key, obs_entities):
    r = _post(client, read_key, _base_payload(obs_entities))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_returns_new_disposition(client, write_key, obs_entities):
    raw, _ = write_key
    r = _post(client, raw, _base_payload(obs_entities))
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
    r = _post(client, raw, payload)
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
    r = _post(
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
    await db.execute("DELETE FROM role_assignments WHERE role_id=$1", role_null)
    await db.execute("DELETE FROM roles WHERE id=$1", role_null)


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
    r1 = _post(client, raw, payload)
    assert r1.json()["disposition"] == "new"
    aid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == aid


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
    r1 = _post(client, raw, payload)
    assert r1.json()["disposition"] == "new"
    aid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == aid

    await db.execute("DELETE FROM role_assignments WHERE role_id=$1", role_b)
    await db.execute("DELETE FROM roles WHERE id=$1", role_b)


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_on_unknown_person(client, write_key, obs_entities):
    raw, _ = write_key
    r = _post(
        client,
        raw,
        {"person_id": generate_id(), "role_id": obs_entities["role_id"]},
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_on_unknown_role(client, write_key, obs_entities):
    raw, _ = write_key
    r = _post(
        client,
        raw,
        {"person_id": obs_entities["person_id"], "role_id": generate_id()},
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_is_current_with_end_date_rejected(client, write_key, obs_entities):
    """is_current=True + end_date set → 422 from model validator."""
    raw, _ = write_key
    r = _post(
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


def test_date_order_validation(client, write_key, obs_entities):
    """start_date after end_date → 422."""
    raw, _ = write_key
    r = _post(
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
    r = _post(
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
    _post(client, raw, payload)
    _post(client, raw, payload)
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
    r = _post(
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


async def test_address_written(client, write_key, obs_entities, db):
    raw, _ = write_key
    r = _post(
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
    yield {"raw_key": raw_key, "key_id": kid}
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


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
    yield {"before_seq": before_seq, "asgn_id": asgn_id}
    await db.execute(
        "DELETE FROM api_key_entity_subscriptions WHERE api_key_id=$1 AND entity_id=$2",
        kid,
        asgn_id,
    )
    await db.execute("DELETE FROM role_assignments WHERE id=$1", asgn_id)
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


async def test_changes_includes_role_assignments(client, changes_api_key, asgn_change_fixtures):
    r = client.get(
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
    yield asgn_id
    await db.execute("DELETE FROM role_assignments WHERE id=$1", asgn_id)
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


async def test_pm_assignment_id_auto_attached(client, write_key, pm_target_assignment):
    """pm_assignment_id targets an existing assignment by PM ULID → auto-attached."""
    raw, _ = write_key
    r = _post(
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
    r = _post(
        client,
        raw,
        {
            "identifier_type": "pm_assignment_id",
            "identifier_value": generate_id(),
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


def test_pm_assignment_id_requires_identifier_value(client, write_key):
    """identifier_type=pm_assignment_id without identifier_value → 422."""
    raw, _ = write_key
    r = _post(client, raw, {"identifier_type": "pm_assignment_id"})
    assert r.status_code == 422
