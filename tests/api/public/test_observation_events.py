"""Integration tests for observation events surface — POST /api/v1/{people,orgs}/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

_PEOPLE_BASE = "/api/v1/people/observations"
_ORGS_BASE = "/api/v1/orgs/observations"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def evt_obs_scope(db):
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
async def evt_write_key(db, evt_obs_scope):
    """API key with observations:write scope."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "evt_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Evt Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, evt_obs_scope
    )
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def evt_read_key(db):
    """Read-only API key (no scope) for 403 scope checks."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "evt_obs_read@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Evt Read Key",
        raw[:8],
        key_hash,
    )
    yield raw
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_people(client, raw_key, payload):
    return client.post(_PEOPLE_BASE, json=payload, headers={"X-API-Key": raw_key})


def _post_orgs(client, raw_key, payload):
    return client.post(_ORGS_BASE, json=payload, headers={"X-API-Key": raw_key})


def _unique_id() -> str:
    return "evt_" + os.urandom(6).hex()


# ---------------------------------------------------------------------------
# Test 1: People observation with birth event creates row
# ---------------------------------------------------------------------------


async def test_people_observation_birth_event_creates_row(client, evt_write_key, db):
    raw, _ = evt_write_key
    value = _unique_id()
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "birth", "event_year": 1965}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] != "rejected"
    eid = body["entity_id"]

    row = await db.fetchrow(
        """SELECT ee.event_year, eet.slug
           FROM entity_events ee
           JOIN entity_event_types eet ON eet.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND ee.entity_type='person'""",
        eid,
    )
    assert row is not None
    assert row["slug"] == "birth"
    assert row["event_year"] == 1965


# ---------------------------------------------------------------------------
# Test 2: People observation event dedup — same event twice → 1 row
# ---------------------------------------------------------------------------


async def test_people_observation_event_dedup(client, evt_write_key, db):
    raw, _ = evt_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "person_wa_pdc",
        "identifier_value": value,
        "events": [{"event_type_slug": "birth", "event_year": 1970}],
    }

    r1 = _post_people(client, raw, payload)
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = _post_people(client, raw, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types eet ON eet.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND eet.slug='birth' AND ee.event_year=1970""",
        eid,
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Test 3: Conflicting events land as separate rows (different year)
# ---------------------------------------------------------------------------


async def test_people_observation_conflicting_events_both_land(client, evt_write_key, db):
    raw, _ = evt_write_key
    value = _unique_id()

    r1 = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "birth", "event_year": 1965}],
        },
    )
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "birth", "event_year": 1966}],
        },
    )
    assert r2.status_code == 200

    count = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types eet ON eet.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND eet.slug='birth'""",
        eid,
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Test 4: Org observation with founded event
# ---------------------------------------------------------------------------


async def test_org_observation_founded_event_creates_row(client, evt_write_key, db):
    raw, _ = evt_write_key
    value = _unique_id()
    r = _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "event_year": 1990}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] != "rejected"
    eid = body["entity_id"]

    row = await db.fetchrow(
        """SELECT ee.event_year, eet.slug
           FROM entity_events ee
           JOIN entity_event_types eet ON eet.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND ee.entity_type='organization'""",
        eid,
    )
    assert row is not None
    assert row["slug"] == "founded"
    assert row["event_year"] == 1990


# ---------------------------------------------------------------------------
# Test 5: applies_to mismatch → rejected
# ---------------------------------------------------------------------------


async def test_applies_to_mismatch_rejected(client, evt_write_key):
    """founded applies_to=organization; posting to people → rejected."""
    raw, _ = evt_write_key
    value = _unique_id()
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "event_year": 1990}],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 6: requires_year missing → rejected
# ---------------------------------------------------------------------------


async def test_requires_year_missing_rejected(client, evt_write_key):
    """birth requires_year=TRUE; omitting event_year → rejected."""
    raw, _ = evt_write_key
    value = _unique_id()
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "birth"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 7: requires_linked_entity missing → rejected
# ---------------------------------------------------------------------------


async def test_requires_linked_entity_missing_rejected(client, evt_write_key):
    """marriage requires_linked_entity=TRUE; omitting linked_entity_id → rejected."""
    raw, _ = evt_write_key
    value = _unique_id()
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": value,
            "events": [{"event_type_slug": "marriage"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 8: Partial date chain — month without year → rejected
# ---------------------------------------------------------------------------


async def test_event_with_month_but_no_year_is_rejected(client, evt_write_key):
    """POST observation with event_month but no event_year hits DB constraint → rejected."""
    raw, _ = evt_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "person_wa_pdc",
        "identifier_value": value,
        "events": [{"event_type_slug": "birth", "event_year": None, "event_month": 6}],
    }
    r = _post_people(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 9: Unknown event_type_slug → rejected
# ---------------------------------------------------------------------------


async def test_unknown_event_type_slug_is_rejected(client, evt_write_key):
    """POST observation with unknown event_type_slug → disposition: rejected."""
    raw, _ = evt_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "person_wa_pdc",
        "identifier_value": value,
        "events": [{"event_type_slug": "nonexistent_type_xyz"}],
    }
    r = _post_people(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 10: Scope enforcement
# ---------------------------------------------------------------------------


async def test_events_scope_enforcement(client, evt_read_key):
    """Read-only key → 403."""
    r = _post_people(
        client,
        evt_read_key,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": _unique_id(),
            "events": [{"event_type_slug": "birth", "event_year": 1980}],
        },
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Test 11: linked_entity_pair validation — 422 when only one of the pair is given
# ---------------------------------------------------------------------------


async def test_linked_entity_id_without_type_is_422(client, evt_write_key):
    """Providing linked_entity_id without linked_entity_type → 422 (Pydantic validator)."""
    raw, _ = evt_write_key
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": _unique_id(),
            "events": [
                {
                    "event_type_slug": "marriage",
                    "linked_entity_id": "01SOMEENTITYID00000000000",
                }
            ],
        },
    )
    assert r.status_code == 422


async def test_linked_entity_type_without_id_is_422(client, evt_write_key):
    """Providing linked_entity_type without linked_entity_id → 422 (Pydantic validator)."""
    raw, _ = evt_write_key
    r = _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": _unique_id(),
            "events": [
                {
                    "event_type_slug": "marriage",
                    "linked_entity_type": "person",
                }
            ],
        },
    )
    assert r.status_code == 422
