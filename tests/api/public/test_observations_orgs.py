"""Integration tests for POST /api/v1/orgs/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_BASE = "/api/v1/orgs/observations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def org_obs_scope(db):
    """Ensure observations:write scope type exists."""
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
async def org_write_key(db, org_obs_scope):
    """API key with observations:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "org_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Org Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, org_obs_scope
    )
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def org_read_key(db):
    """Read-only API key (no scope) for 403 scope checks."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "org_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Org Read Key",
        raw[:8],
        key_hash,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _post(client, raw_key, payload):
    return client.post(_BASE, json=payload, headers={"X-API-Key": raw_key})


def _unique_id() -> str:
    return "org_" + os.urandom(6).hex()


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_org_returns_new_disposition(client, org_write_key):
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "organization"


# ---------------------------------------------------------------------------
# Disposition — AUTO_ATTACHED
# ---------------------------------------------------------------------------


async def test_auto_attached_on_second_observation(client, org_write_key):
    raw, _ = org_write_key
    value = _unique_id()
    payload = {"identifier_type": "org_ubi", "identifier_value": value}

    r1 = _post(client, raw, payload)
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"
    eid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == eid


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_on_unknown_identifier_type(client, org_write_key):
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "zzz_nonexistent_xyz", "identifier_value": "v"})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["entity_id"] is None


async def test_rejected_on_wrong_entity_type(client, org_write_key):
    """person_wa_pdc is a person identifier → rejected on /orgs/observations."""
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "person_wa_pdc", "identifier_value": _unique_id()})
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Org acronym
# ---------------------------------------------------------------------------


async def test_org_acronym_created(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "org_acronyms": ["TSO"],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE organization_id=$1 AND acronym='TSO'",
        eid,
    )
    assert row is not None
    assert row["is_canonical"] is False


async def test_org_acronym_no_duplicate(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "org_ubi",
        "identifier_value": value,
        "org_acronyms": ["NDO"],
    }
    r1 = _post(client, raw, payload)
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM organization_acronyms WHERE organization_id=$1 AND acronym='NDO'",
        eid,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Org parent
# ---------------------------------------------------------------------------


async def test_org_parent_by_id(client, org_write_key, db):
    raw, _ = org_write_key

    r_parent = _post(client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()})
    assert r_parent.status_code == 200
    parent_id = r_parent.json()["entity_id"]

    r_child = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "organization_parent_id": parent_id,
        },
    )
    assert r_child.status_code == 200
    child_id = r_child.json()["entity_id"]

    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", child_id)
    assert row["parent_id"] == parent_id


async def test_org_parent_by_name_ambiguous_rejected(client, org_write_key, db):
    """Two orgs with same canonical name → parent_name lookup → rejected."""
    raw, kid = org_write_key
    shared_name = "Ambiguous Org " + _unique_id()

    org_a = generate_id()
    org_b = generate_id()
    name_a = generate_id()
    name_b = generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_a)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_b)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical,"
        " source_key_id) VALUES ($1, $2, $3, 'legal', TRUE, $4)",
        name_a,
        org_a,
        shared_name,
        kid,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical,"
        " source_key_id) VALUES ($1, $2, $3, 'legal', TRUE, $4)",
        name_b,
        org_b,
        shared_name,
        kid,
    )

    try:
        r = _post(
            client,
            raw,
            {
                "identifier_type": "org_ubi",
                "identifier_value": _unique_id(),
                "organization_parent_name": shared_name,
            },
        )
        assert r.status_code == 200
        assert r.json()["disposition"] == "rejected"
    finally:
        await db.execute("DELETE FROM organization_names WHERE id=$1", name_a)
        await db.execute("DELETE FROM organization_names WHERE id=$1", name_b)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_a)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_b)


async def test_xor_org_parent_two_fields_returns_422(client, org_write_key):
    """Supplying two parent fields → 422 at schema validation."""
    raw, _ = org_write_key
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "organization_parent_id": generate_id(),
            "organization_parent_name": "Some Org",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


async def test_link_attached(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()
    url = f"https://example.com/{value}"
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "links": [{"url": url, "link_type_slug": "website"}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        "SELECT COUNT(*) FROM links WHERE entity_type='organization' AND entity_id=$1 AND url=$2",
        eid,
        url,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Additional identifier
# ---------------------------------------------------------------------------


async def test_additional_identifier_attached(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()

    slug = "org_obs_secondary"
    existing = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not existing:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'organization', $2, 'Org Secondary', 'Org Obs Secondary ID')",
            eit_id,
            slug,
        )

    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "additional_identifiers": [
                {"identifier_type_slug": slug, "identifier_value": "extra_" + value}
            ],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        """SELECT COUNT(*) FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE i.entity_id=$1 AND t.slug=$2""",
        eid,
        slug,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_missing_scope_returns_403(client, org_read_key):
    r = _post(
        client, org_read_key, {"identifier_type": "org_ubi", "identifier_value": _unique_id()}
    )
    assert r.status_code == 403
