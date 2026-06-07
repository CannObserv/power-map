"""Integration tests for POST /api/v1/jurisdictions/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

# Sync auth tests live in test_auth.py per project convention.

_BASE = "/api/v1/jurisdictions/observations"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_obs_scope(db):
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
async def jur_write_key(db, jur_obs_scope):
    """API key with observations:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "jur_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Jur Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, jur_obs_scope
    )
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def jur_read_key(db):
    """Read-only API key (no scope) for 403 scope checks."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "jur_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Jur Read Key",
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


def _new_ocd(suffix: str) -> dict:
    """Minimal NEW jurisdiction payload with a unique OCD identifier."""
    return {
        "identifier_type": "jur_ocd",
        "identifier_value": f"ocd-division/country:us/test:{suffix}",
        "jurisdiction_slug": f"test-{suffix}",
        "jurisdiction_name": f"Test Jurisdiction {suffix}",
        "jurisdiction_type_slug": "state",
    }


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_jurisdiction_returns_new_disposition(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    r = _post(client, raw, _new_ocd(suffix))
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "jurisdiction"

    # Verify row in DB
    row = await db.fetchrow(
        "SELECT id, slug, name FROM jurisdictions WHERE id=$1", body["entity_id"]
    )
    assert row is not None
    assert row["slug"] == f"test-{suffix}"
    assert row["name"] == f"Test Jurisdiction {suffix}"

    # Cleanup
    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", body["entity_id"])
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", body["entity_id"])


async def test_new_jurisdiction_sets_type(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    r = _post(client, raw, _new_ocd(suffix))
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        """SELECT jt.slug AS type_slug FROM jurisdictions j
           JOIN jurisdiction_types jt ON jt.id = j.type_id
           WHERE j.id=$1""",
        eid,
    )
    assert row["type_slug"] == "state"

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


async def test_new_jurisdiction_with_optional_fields(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        **_new_ocd(suffix),
        "jurisdiction_valid_from": "2020-01-01",
        "jurisdiction_valid_until": "2030-12-31",
        "jurisdiction_notes": "Test notes",
    }
    r = _post(client, raw, payload)
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT valid_from, valid_until, notes FROM jurisdictions WHERE id=$1", eid
    )
    assert str(row["valid_from"]) == "2020-01-01"
    assert str(row["valid_until"]) == "2030-12-31"
    assert row["notes"] == "Test notes"

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Disposition — AUTO_ATTACHED
# ---------------------------------------------------------------------------


async def test_auto_attached_on_second_observation(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = _new_ocd(suffix)

    r1 = _post(client, raw, payload)
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"
    eid = r1.json()["entity_id"]

    r2 = _post(client, raw, payload)
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == eid

    # Only one jurisdiction row created
    count = await db.fetchval("SELECT COUNT(*) FROM jurisdictions WHERE id=$1", eid)
    assert count == 1

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


async def test_auto_attached_does_not_require_jurisdiction_fields(client, jur_write_key, db):
    """On AUTO_ATTACHED, slug/name/type_slug are not required."""
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()

    r1 = _post(client, raw, _new_ocd(suffix))
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    # Second observation with only identifier fields
    r2 = _post(
        client,
        raw,
        {
            "identifier_type": "jur_ocd",
            "identifier_value": f"ocd-division/country:us/test:{suffix}",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "auto-attached"

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_when_slug_missing_for_new(client, jur_write_key):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        "identifier_type": "jur_ocd",
        "identifier_value": f"ocd-division/country:us/test:{suffix}",
        "jurisdiction_name": "Missing Slug",
        "jurisdiction_type_slug": "state",
        # jurisdiction_slug omitted
    }
    r = _post(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_when_name_missing_for_new(client, jur_write_key):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        "identifier_type": "jur_ocd",
        "identifier_value": f"ocd-division/country:us/test:{suffix}",
        "jurisdiction_slug": f"test-{suffix}",
        "jurisdiction_type_slug": "state",
        # jurisdiction_name omitted
    }
    r = _post(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_when_type_missing_for_new(client, jur_write_key):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        "identifier_type": "jur_ocd",
        "identifier_value": f"ocd-division/country:us/test:{suffix}",
        "jurisdiction_slug": f"test-{suffix}",
        "jurisdiction_name": "Missing Type",
        # jurisdiction_type_slug omitted
    }
    r = _post(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_on_unknown_identifier_type(client, jur_write_key):
    raw, _ = jur_write_key
    r = _post(
        client,
        raw,
        {
            "identifier_type": "jur_unknown_xyz",
            "identifier_value": "anything",
            "jurisdiction_slug": "test-x",
            "jurisdiction_name": "Test",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_on_invalid_type_slug(client, jur_write_key):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        **_new_ocd(suffix),
        "jurisdiction_type_slug": "nonexistent_type",
    }
    r = _post(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_rejected_on_valid_from_after_valid_until(client, jur_write_key):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    payload = {
        **_new_ocd(suffix),
        "jurisdiction_valid_from": "2030-01-01",
        "jurisdiction_valid_until": "2020-01-01",
    }
    r = _post(client, raw, payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Additional identifiers
# ---------------------------------------------------------------------------


async def test_additional_identifier_attached(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    r = _post(
        client,
        raw,
        {
            **_new_ocd(suffix),
            "additional_identifiers": [
                {"identifier_type_slug": "jur_fips", "identifier_value": f"99{suffix[:4]}"},
            ],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        """SELECT COUNT(*) FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE i.entity_id=$1 AND t.slug='jur_fips'""",
        eid,
    )
    assert count == 1

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


async def test_link_attached_to_jurisdiction(client, jur_write_key, db):
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    r = _post(
        client,
        raw,
        {
            **_new_ocd(suffix),
            "links": [{"url": f"https://example.com/{suffix}", "link_type_slug": "website"}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        "SELECT COUNT(*) FROM links WHERE entity_type='jurisdiction' AND entity_id=$1", eid
    )
    assert count == 1

    await db.execute("DELETE FROM links WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_missing_scope_returns_403(client, jur_read_key):
    r = _post(client, jur_read_key, _new_ocd(os.urandom(4).hex()))
    assert r.status_code == 403


async def test_rejected_on_wrong_entity_type(client, jur_write_key):
    """Identifier belonging to a person entity → rejected on /jurisdictions/observations."""
    raw, _ = jur_write_key
    r = _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",  # seeded as entity_type='person'
            "identifier_value": "99999",
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_slug_collision_returns_rejected(client, jur_write_key, db):
    """Two different OCD IDs claiming the same slug → second is rejected."""
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    slug = f"test-collision-{suffix}"

    r1 = _post(
        client,
        raw,
        {
            "identifier_type": "jur_ocd",
            "identifier_value": f"ocd-division/country:us/test:a{suffix}",
            "jurisdiction_slug": slug,
            "jurisdiction_name": "Collision A",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"
    eid = r1.json()["entity_id"]

    r2 = _post(
        client,
        raw,
        {
            "identifier_type": "jur_ocd",
            "identifier_value": f"ocd-division/country:us/test:b{suffix}",
            "jurisdiction_slug": slug,
            "jurisdiction_name": "Collision B",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "rejected"

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# jur_slug identifier type
# ---------------------------------------------------------------------------


async def test_new_via_jur_slug_returns_new_disposition(client, jur_write_key, db):
    """POST with identifier_type=jur_slug creates a new jurisdiction."""
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    slug = f"test-slug-direct-{suffix}"
    r = _post(
        client,
        raw,
        {
            "identifier_type": "jur_slug",
            "identifier_value": slug,
            "jurisdiction_slug": slug,
            "jurisdiction_name": f"Test Slug Direct {suffix}",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_type"] == "jurisdiction"

    row = await db.fetchrow("SELECT slug FROM jurisdictions WHERE id=$1", body["entity_id"])
    assert row["slug"] == slug

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", body["entity_id"])
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", body["entity_id"])


async def test_new_via_jur_ocd_auto_registers_jur_slug_identifier(client, jur_write_key, db):
    """NEW via jur_ocd → jur_slug identifier row auto-inserted for cross-type matching."""
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    slug = f"test-slug-reg-{suffix}"
    r = _post(
        client,
        raw,
        {
            "identifier_type": "jur_ocd",
            "identifier_value": f"ocd-division/country:us/test:{suffix}",
            "jurisdiction_slug": slug,
            "jurisdiction_name": f"Test Slug Reg {suffix}",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        """SELECT COUNT(*) FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND t.slug = 'jur_slug'""",
        eid,
    )
    assert count == 1

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)


async def test_auto_attach_via_jur_slug_after_jur_ocd_creation(client, jur_write_key, db):
    """Jurisdiction created via jur_ocd → subsequent jur_slug observation AUTO_ATTACHes."""
    raw, _ = jur_write_key
    suffix = os.urandom(4).hex()
    slug = f"test-slug-attach-{suffix}"

    r1 = _post(
        client,
        raw,
        {
            "identifier_type": "jur_ocd",
            "identifier_value": f"ocd-division/country:us/test:{suffix}",
            "jurisdiction_slug": slug,
            "jurisdiction_name": f"Test Slug Attach {suffix}",
            "jurisdiction_type_slug": "state",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"
    eid = r1.json()["entity_id"]

    r2 = _post(
        client,
        raw,
        {"identifier_type": "jur_slug", "identifier_value": slug},
    )
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == eid

    await db.execute("DELETE FROM identifiers WHERE entity_id=$1", eid)
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", eid)
