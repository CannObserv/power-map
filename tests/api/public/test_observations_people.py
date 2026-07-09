"""Integration tests for POST /api/v1/people/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

_BASE = "/api/v1/people/observations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def ppl_obs_scope(db):
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
    return scope_id


@pytest_asyncio.fixture(loop_scope="session")
async def ppl_write_key(db, ppl_obs_scope):
    """API key with observations:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "ppl_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Ppl Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, ppl_obs_scope
    )
    return raw, kid


@pytest_asyncio.fixture(loop_scope="session")
async def ppl_read_key(db):
    """Read-only API key (no scope) for 403 scope checks."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "ppl_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Ppl Read Key",
        raw[:8],
        key_hash,
    )
    return raw


@pytest_asyncio.fixture(loop_scope="session")
async def ppl_role_id(db):
    """Minimal org + role for role_assignment tests."""
    org_id = generate_id()
    role_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Ppl Obs Role')",
        role_id,
        org_id,
    )
    return role_id


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _post(client, raw_key, payload):
    return client.post(_BASE, json=payload, headers={"X-API-Key": raw_key})


def _unique_id() -> str:
    return "ppl_" + os.urandom(6).hex()


# ---------------------------------------------------------------------------
# Disposition — NEW
# ---------------------------------------------------------------------------


async def test_new_person_returns_new_disposition(client, ppl_write_key):
    raw, _ = ppl_write_key
    r = await _post(
        client, raw, {"identifier_type": "person_wa_pdc", "identifier_value": _unique_id()}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "person"


# ---------------------------------------------------------------------------
# Disposition — AUTO_ATTACHED
# ---------------------------------------------------------------------------


async def test_auto_attached_on_second_observation(client, ppl_write_key):
    raw, _ = ppl_write_key
    value = _unique_id()
    payload = {"identifier_type": "person_wa_pdc", "identifier_value": value}

    r1 = await _post(client, raw, payload)
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"
    eid = r1.json()["entity_id"]

    r2 = await _post(client, raw, payload)
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "auto-attached"
    assert r2.json()["entity_id"] == eid


# ---------------------------------------------------------------------------
# Disposition — REJECTED
# ---------------------------------------------------------------------------


async def test_rejected_on_unknown_identifier_type(client, ppl_write_key):
    raw, _ = ppl_write_key
    r = await _post(
        client, raw, {"identifier_type": "zzz_nonexistent_xyz", "identifier_value": "v"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["entity_id"] is None


async def test_rejected_on_wrong_entity_type(client, ppl_write_key):
    """org_ubi is an organization identifier → rejected on /people/observations."""
    raw, _ = ppl_write_key
    r = await _post(client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()})
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Name claim
# ---------------------------------------------------------------------------


async def test_name_claim_creates_row(client, ppl_write_key, db):
    raw, kid = ppl_write_key
    value = _unique_id()
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "names": [{"name": "Jane Doe", "name_type": "legal"}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT visibility, source_key_id FROM person_names WHERE person_id=$1 AND name='Jane Doe'",
        eid,
    )
    assert row is not None
    assert row["visibility"] == "public"
    assert row["source_key_id"] == kid


async def test_name_claim_no_duplicate(client, ppl_write_key, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "person_wa_pdc",
        "identifier_value": value,
        "names": [{"name": "John Smith", "name_type": "legal"}],
    }
    r1 = await _post(client, raw, payload)
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = await _post(client, raw, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM person_names WHERE person_id=$1 AND name='John Smith'", eid
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Pronouns
# ---------------------------------------------------------------------------


async def test_pronouns_set(client, ppl_write_key, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "personal_pronouns": "they/them",
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", eid)
    assert row["personal_pronouns"] == "they/them"


async def test_pronouns_write_if_null(client, ppl_write_key, db):
    raw, _ = ppl_write_key
    value = _unique_id()

    r1 = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "personal_pronouns": "she/her",
        },
    )
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "personal_pronouns": "he/him",
        },
    )
    assert r2.status_code == 200

    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", eid)
    assert row["personal_pronouns"] == "she/her"


# ---------------------------------------------------------------------------
# Role assignment
# ---------------------------------------------------------------------------


async def test_role_assignment_created(client, ppl_write_key, ppl_role_id, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "role_assignments": [{"role_id": ppl_role_id}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        eid,
        ppl_role_id,
    )
    assert row is not None


async def test_role_assignment_no_duplicate(client, ppl_write_key, ppl_role_id, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "person_wa_pdc",
        "identifier_value": value,
        "role_assignments": [{"role_id": ppl_role_id}],
    }
    r1 = await _post(client, raw, payload)
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = await _post(client, raw, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND end_date IS NULL AND archived_at IS NULL",
        eid,
        ppl_role_id,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


async def test_link_attached(client, ppl_write_key, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    url = f"https://example.com/{value}"
    r = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "links": [{"url": url, "link_type_slug": "website"}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    count = await db.fetchval(
        "SELECT COUNT(*) FROM links WHERE entity_type='person' AND entity_id=$1 AND url=$2",
        eid,
        url,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Additional identifier
# ---------------------------------------------------------------------------


async def test_additional_identifier_attached(client, ppl_write_key, db):
    raw, _ = ppl_write_key
    value = _unique_id()
    extra = "extra_" + value

    # Ensure a second person identifier type exists for the additional_identifier
    slug = "person_ppl_obs_secondary"
    existing = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not existing:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'person', $2, 'Ppl Secondary', 'People Obs Secondary ID')",
            eit_id,
            slug,
        )

    r = await _post(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "additional_identifiers": [{"identifier_type_slug": slug, "identifier_value": extra}],
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


async def test_missing_scope_returns_403(client, ppl_read_key):
    r = await _post(
        client,
        ppl_read_key,
        {"identifier_type": "person_wa_pdc", "identifier_value": _unique_id()},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# #225 — reason field on rejected observations
# ---------------------------------------------------------------------------


async def test_rejected_unknown_type_includes_reason(client, ppl_write_key):
    """Unknown identifier type rejection must include a reason string."""
    raw, _ = ppl_write_key
    r = await _post(
        client, raw, {"identifier_type": "zzz_nonexistent_xyz", "identifier_value": "v"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None
    assert "zzz_nonexistent_xyz" in body["reason"]
