"""Integration tests for POST /api/v1/roles/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_BASE = "/api/v1/roles/observations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def role_obs_scope(db):
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
async def role_write_key(db, role_obs_scope):
    """API key with observations:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "role_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Role Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, role_obs_scope
    )
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def role_read_key(db):
    """Read-only API key (no scope) for 403 checks."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "role_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Role Read Key",
        raw[:8],
        key_hash,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def obs_org(db):
    """Org used as the owner for role observations."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    yield org_id
    await db.execute("DELETE FROM roles WHERE organization_id=$1", org_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(client, raw_key, payload):
    return client.post(_BASE, json=payload, headers={"X-API-Key": raw_key})


def _title() -> str:
    return "Role_" + os.urandom(4).hex()


# ---------------------------------------------------------------------------
# Auth / scope
# ---------------------------------------------------------------------------


def test_obs_requires_api_key(client):
    r = client.post(_BASE, json={"organization_id": "x", "title": "x"})
    assert r.status_code == 403


async def test_obs_requires_write_scope(client, role_read_key, obs_org):
    r = _post(client, role_read_key, {"organization_id": obs_org, "title": _title()})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_role_returns_new_disposition(client, role_write_key, obs_org):
    raw, _ = role_write_key
    r = _post(client, raw, {"organization_id": obs_org, "title": _title()})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "role"


async def test_new_role_persisted(client, role_write_key, obs_org, db):
    raw, _ = role_write_key
    title = _title()
    r = _post(client, raw, {"organization_id": obs_org, "title": title})
    rid = r.json()["entity_id"]
    row = await db.fetchrow("SELECT title, organization_id FROM roles WHERE id=$1", rid)
    assert row["title"] == title
    assert row["organization_id"] == obs_org


async def test_new_role_with_metadata(client, role_write_key, obs_org, db):
    raw, _ = role_write_key
    title = _title()
    r = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "notes": "A notes field",
            "established_on": "2022-03-15",
            "abolished_on": "2024-06-30",
        },
    )
    assert r.status_code == 200
    rid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT notes, established_on, abolished_on FROM roles WHERE id=$1", rid
    )
    assert row["notes"] == "A notes field"
    assert str(row["established_on"]) == "2022-03-15"
    assert str(row["abolished_on"]) == "2024-06-30"


# ---------------------------------------------------------------------------
# Disposition — AUTO_ATTACHED
# ---------------------------------------------------------------------------


async def test_auto_attached_on_duplicate_title(client, role_write_key, obs_org):
    raw, _ = role_write_key
    title = _title()
    payload = {"organization_id": obs_org, "title": title}
    r1 = _post(client, raw, payload)
    assert r1.json()["disposition"] == "new"
    rid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == rid


async def test_auto_attached_case_insensitive(client, role_write_key, obs_org):
    raw, _ = role_write_key
    base = _title()
    r1 = _post(client, raw, {"organization_id": obs_org, "title": base})
    rid = r1.json()["entity_id"]

    r2 = _post(client, raw, {"organization_id": obs_org, "title": base.upper()})
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == rid


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_on_unknown_org(client, role_write_key):
    raw, _ = role_write_key
    r = _post(client, raw, {"organization_id": generate_id(), "title": _title()})
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Attribute writes — links
# ---------------------------------------------------------------------------


async def test_link_written_on_new_role(client, role_write_key, obs_org, link_type, db):
    raw, _ = role_write_key
    title = _title()
    r = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "links": [{"url": "https://example.com/role", "link_type_slug": "website"}],
        },
    )
    assert r.json()["disposition"] == "new"
    rid = r.json()["entity_id"]
    link = await db.fetchrow("SELECT url FROM links WHERE entity_type='role' AND entity_id=$1", rid)
    assert link["url"] == "https://example.com/role"


async def test_link_deduped_on_auto_attached(client, role_write_key, obs_org, db):
    raw, _ = role_write_key
    title = _title()
    payload = {
        "organization_id": obs_org,
        "title": title,
        "links": [{"url": "https://example.com/dup", "link_type_slug": "website"}],
    }
    _post(client, raw, payload)
    _post(client, raw, payload)
    count = await db.fetchval(
        "SELECT COUNT(*) FROM links l"
        " JOIN roles r ON r.id=l.entity_id"
        " WHERE l.entity_type='role' AND r.title=$1 AND r.organization_id=$2"
        "   AND l.url='https://example.com/dup'",
        title,
        obs_org,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Attribute writes — contact_methods
# ---------------------------------------------------------------------------


async def test_contact_method_written(client, role_write_key, obs_org, db):
    raw, _ = role_write_key
    title = _title()
    r = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "contact_methods": [{"contact_type": "email", "value": "chair@example.com"}],
        },
    )
    assert r.json()["disposition"] == "new"
    rid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT value FROM contact_methods WHERE entity_type='role' AND entity_id=$1", rid
    )
    assert row is not None


async def test_contact_method_written_on_auto_attached(client, role_write_key, obs_org, db):
    raw, _ = role_write_key
    title = _title()
    base_payload = {
        "organization_id": obs_org,
        "title": title,
        "contact_methods": [{"contact_type": "email", "value": "first@example.com"}],
    }
    r1 = _post(client, raw, base_payload)
    assert r1.json()["disposition"] == "new"
    rid = r1.json()["entity_id"]

    r2 = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "contact_methods": [{"contact_type": "email", "value": "second@example.com"}],
        },
    )
    assert r2.json()["disposition"] == "auto-attached"
    count = await db.fetchval(
        "SELECT COUNT(*) FROM contact_methods WHERE entity_type='role' AND entity_id=$1", rid
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Attribute writes — addresses
# ---------------------------------------------------------------------------


async def test_address_written(client, role_write_key, obs_org, db, local_address_normalizer):
    raw, _ = role_write_key
    title = _title()
    r = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "addresses": [{"raw_input": "1600 Pennsylvania Ave NW, Washington DC 20500"}],
        },
    )
    assert r.json()["disposition"] == "new"
    rid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT ea.id FROM entity_addresses ea WHERE ea.entity_type='role' AND ea.entity_id=$1",
        rid,
    )
    assert row is not None


async def test_address_written_on_auto_attached(
    client, role_write_key, obs_org, db, local_address_normalizer
):
    raw, _ = role_write_key
    title = _title()
    r1 = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "addresses": [{"raw_input": "100 Main St"}],
        },
    )
    assert r1.json()["disposition"] == "new"
    rid = r1.json()["entity_id"]

    r2 = _post(
        client,
        raw,
        {
            "organization_id": obs_org,
            "title": title,
            "addresses": [{"raw_input": "200 Oak Ave"}],
        },
    )
    assert r2.json()["disposition"] == "auto-attached"
    count = await db.fetchval(
        "SELECT COUNT(*) FROM entity_addresses WHERE entity_type='role' AND entity_id=$1", rid
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_obs_date_order_validation(client, role_write_key):
    """established_on after abolished_on → 422 from Pydantic model validator."""
    raw, _ = role_write_key
    r = _post(
        client,
        raw,
        {
            "organization_id": "ignored",
            "title": "irrelevant",
            "established_on": "2025-06-01",
            "abolished_on": "2024-01-01",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PM-native identifier (#198)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def pm_target_role(db, obs_org):
    """An existing role for pm_role_id tests."""
    role_id = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        role_id,
        obs_org,
        "PM Target Role",
    )
    yield role_id
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)


async def test_pm_role_id_auto_attached(client, role_write_key, pm_target_role):
    """identifier_type=pm_role_id targets an existing role by PM ULID → auto-attached."""
    raw, _ = role_write_key
    r = _post(client, raw, {"identifier_type": "pm_role_id", "identifier_value": pm_target_role})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == pm_target_role
    assert body["entity_type"] == "role"


async def test_pm_role_id_rejected_on_unknown_ulid(client, role_write_key):
    """identifier_type=pm_role_id with unknown ULID → rejected."""
    raw, _ = role_write_key
    r = _post(client, raw, {"identifier_type": "pm_role_id", "identifier_value": generate_id()})
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


def test_pm_role_id_requires_identifier_value(client, role_write_key):
    """identifier_type=pm_role_id without identifier_value → 422."""
    raw, _ = role_write_key
    r = _post(client, raw, {"identifier_type": "pm_role_id"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# #225 — reason field on rejected observations
# ---------------------------------------------------------------------------


async def test_rejected_unknown_org_includes_reason(client, role_write_key):
    """Unknown organization_id rejection must include a reason string."""
    raw, _ = role_write_key
    unknown_org_id = generate_id()
    r = _post(client, raw, {"organization_id": unknown_org_id, "title": "Test Role"})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None
    assert "org_not_found" in body["reason"]
