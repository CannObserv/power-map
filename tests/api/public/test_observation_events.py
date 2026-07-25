"""Integration tests for observation events surface — POST /api/v1/{people,orgs}/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
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
    return scope_id


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
    return raw, kid


@pytest_asyncio.fixture(loop_scope="session")
async def evt_write_key2(db, evt_obs_scope):
    """A second, distinct API key with observations:write scope (provenance tests)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "evt_obs2@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Evt Obs Key 2",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, evt_obs_scope
    )
    return raw, kid


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
    return raw


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
    r = await _post_people(
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

    r1 = await _post_people(client, raw, payload)
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    r2 = await _post_people(client, raw, payload)
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

    r1 = await _post_people(
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

    r2 = await _post_people(
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
    r = await _post_orgs(
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
    r = await _post_people(
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
    r = await _post_people(
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
    r = await _post_people(
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
    r = await _post_people(client, raw, payload)
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
    r = await _post_people(client, raw, payload)
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# Test 10: Scope enforcement
# ---------------------------------------------------------------------------


async def test_events_scope_enforcement(client, evt_read_key):
    """Read-only key → 403."""
    r = await _post_people(
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
    r = await _post_people(
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
    r = await _post_people(
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


# ---------------------------------------------------------------------------
# Tests 13–15: event_place_address_id validation via observation write
# ---------------------------------------------------------------------------


async def test_event_place_address_id_not_found_is_rejected(client, evt_write_key):
    """Submitting a non-existent event_place_address_id → disposition: rejected."""
    raw, _ = evt_write_key
    r = await _post_people(
        client,
        raw,
        {
            "identifier_type": "person_wa_pdc",
            "identifier_value": _unique_id(),
            "events": [
                {
                    "event_type_slug": "birth",
                    "event_year": 1980,
                    "event_place_address_id": "01NONEXISTENTADDRESSID00000",
                }
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_event_place_address_id_low_precision_is_rejected(client, evt_write_key, db):
    """Address with region precision → disposition: rejected."""
    raw, _ = evt_write_key
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, region, country, precision)"
        " VALUES ($1, 'Oregon', 'OR', 'US', 'region')",
        aid,
    )
    try:
        r = await _post_people(
            client,
            raw,
            {
                "identifier_type": "person_wa_pdc",
                "identifier_value": _unique_id(),
                "events": [
                    {
                        "event_type_slug": "birth",
                        "event_year": 1980,
                        "event_place_address_id": aid,
                    }
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["disposition"] == "rejected"
    finally:
        await db.execute("DELETE FROM addresses WHERE id=$1", aid)


# ---------------------------------------------------------------------------
# #321 — pm_event_id refine-in-place, provenance, immutable identity, succeeded_by
# ---------------------------------------------------------------------------


async def _org_with_founded(client, raw, db, year):
    """Create an org (by org_ubi) carrying one founded event; return (ubi, org_id, event_id)."""
    value = _unique_id()
    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "event_year": year}],
        },
    )
    assert r.status_code == 200, r.text
    org_id = r.json()["entity_id"]
    ev = await db.fetchrow(
        """SELECT ee.id FROM entity_events ee JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND ee.entity_type='organization' AND t.slug='founded'""",
        org_id,
    )
    return value, org_id, ev["id"]


async def test_pm_event_id_refines_year_in_place(client, evt_write_key, db):
    """A founded year sharpening 2013→2011 via pm_event_id updates in place — no dup."""
    raw, _ = evt_write_key
    value, org_id, event_id = await _org_with_founded(client, raw, db, 2013)

    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "pm_event_id": event_id, "event_year": 2011}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["disposition"] == "updated"

    rows = await db.fetch(
        """SELECT ee.event_year FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded'""",
        org_id,
    )
    assert len(rows) == 1
    assert rows[0]["event_year"] == 2011


async def test_pm_event_id_identical_reemit_is_noop(client, evt_write_key, db):
    """An identical pm_event_id re-emit is auto-attached and does not bump updated_at."""
    raw, _ = evt_write_key
    value, _org_id, event_id = await _org_with_founded(client, raw, db, 2013)
    before = await db.fetchval("SELECT updated_at FROM entity_events WHERE id=$1", event_id)

    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "pm_event_id": event_id, "event_year": 2013}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["disposition"] == "auto-attached"
    after = await db.fetchval("SELECT updated_at FROM entity_events WHERE id=$1", event_id)
    assert before == after


async def test_pm_event_id_foreign_source_rejected(client, evt_write_key, evt_write_key2, db):
    """A different key updating a source-stamped event → rejected / provenance_conflict."""
    raw1, _ = evt_write_key
    raw2, _ = evt_write_key2
    value, org_id, event_id = await _org_with_founded(client, raw1, db, 2013)

    r = await _post_orgs(
        client,
        raw2,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "pm_event_id": event_id, "event_year": 2011}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "provenance_conflict"
    # unchanged
    yr = await db.fetchval("SELECT event_year FROM entity_events WHERE id=$1", event_id)
    assert yr == 2013


async def test_pm_event_id_change_event_type_is_identity_immutable(client, evt_write_key, db):
    """Re-typing an id-addressed event → rejected / identity_immutable, no reclassify."""
    raw, _ = evt_write_key
    value, _org_id, event_id = await _org_with_founded(client, raw, db, 2013)

    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [
                {"event_type_slug": "dissolved", "pm_event_id": event_id, "event_year": 2020}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "identity_immutable"
    slug = await db.fetchval(
        "SELECT t.slug FROM entity_events ee JOIN entity_event_types t ON t.id=ee.event_type_id"
        " WHERE ee.id=$1",
        event_id,
    )
    assert slug == "founded"


async def test_pm_event_id_not_found_rejected(client, evt_write_key, db):
    """pm_event_id that does not resolve → rejected / event_not_found."""
    raw, _ = evt_write_key
    value = _unique_id()
    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [
                {
                    "event_type_slug": "founded",
                    "pm_event_id": "01NONEXISTENTEVENTID0000000",
                    "event_year": 2011,
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "event_not_found"


async def test_succeeded_by_unresolved_successor_rejected(client, evt_write_key, db):
    """succeeded_by whose successor isn't anchored yet → rejected / linked_entity_unresolved."""
    raw, _ = evt_write_key
    value = _unique_id()
    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [
                {
                    "event_type_slug": "succeeded_by",
                    "linked_entity_type": "organization",
                    "linked_entity_id": "01NONEXISTENTORGID00000000",
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "linked_entity_unresolved"


async def test_succeeded_by_resolved_successor_creates(client, evt_write_key, db):
    """succeeded_by with an anchored successor → event created on the predecessor."""
    raw, _ = evt_write_key
    # successor org
    succ_ubi = _unique_id()
    rs = await _post_orgs(client, raw, {"identifier_type": "org_ubi", "identifier_value": succ_ubi})
    successor_id = rs.json()["entity_id"]
    # predecessor + succeeded_by link
    value = _unique_id()
    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [
                {
                    "event_type_slug": "succeeded_by",
                    "linked_entity_type": "organization",
                    "linked_entity_id": successor_id,
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] != "rejected"
    assert body["events"][0]["disposition"] == "new"
    pred_id = body["entity_id"]
    row = await db.fetchrow(
        """SELECT ee.linked_entity_id FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='succeeded_by'""",
        pred_id,
    )
    assert row is not None
    assert row["linked_entity_id"] == successor_id


# ---------------------------------------------------------------------------
# #321 — thin POST /orgs/{org_id}/events/observations (partial-success)
# ---------------------------------------------------------------------------


def _post_org_events(client, raw_key, org_id, events):
    return client.post(
        f"/api/v1/orgs/{org_id}/events/observations",
        json={"events": events},
        headers={"X-API-Key": raw_key},
    )


async def _make_org(client, raw, db):
    """Create a bare org via the org observation surface; return its PM id."""
    r = await _post_orgs(
        client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()}
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


async def test_org_events_observations_partial_success(client, evt_write_key, db):
    """A batch of [good, bad] → good commits, bad reported, no rollback of good."""
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)

    r = await _post_org_events(
        client,
        raw,
        org_id,
        [
            {"event_type_slug": "founded", "event_year": 1995},
            {
                "event_type_slug": "succeeded_by",
                "linked_entity_type": "organization",
                "linked_entity_id": "01NONEXISTENTORGID00000000",
            },
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "new"
    assert results[1]["disposition"] == "rejected"
    assert results[1]["reason"] == "linked_entity_unresolved"

    # the good event landed despite the sibling rejection
    n = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded' AND ee.event_year=1995""",
        org_id,
    )
    assert n == 1


async def test_org_events_observations_refine_in_place(client, evt_write_key, db):
    """pm_event_id refine works on the thin surface too."""
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    r1 = await _post_org_events(
        client, raw, org_id, [{"event_type_slug": "founded", "event_year": 2013}]
    )
    event_id = r1.json()["results"][0]["event_id"]

    r2 = await _post_org_events(
        client,
        raw,
        org_id,
        [{"event_type_slug": "founded", "pm_event_id": event_id, "event_year": 2011}],
    )
    assert r2.json()["results"][0]["disposition"] == "updated"
    yr = await db.fetchval("SELECT event_year FROM entity_events WHERE id=$1", event_id)
    assert yr == 2011


async def test_org_events_observations_db_constraint_isolated(client, evt_write_key, db):
    """CR #1: a per-event DB constraint violation is isolated, not a 500 batch-abort.

    ``renamed`` has requires_year=False, so month-without-year passes app checks
    and trips the DB chk_month_requires_year on INSERT — must come back rejected/
    invalid while the sibling founded event still lands.
    """
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)

    r = await _post_org_events(
        client,
        raw,
        org_id,
        [
            {"event_type_slug": "founded", "event_year": 1995},
            {"event_type_slug": "renamed", "event_month": 6},  # month w/o year → DB check
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "new"
    assert results[1]["disposition"] == "rejected"
    assert results[1]["reason"] == "invalid"
    n = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded' AND ee.event_year=1995""",
        org_id,
    )
    assert n == 1


async def test_org_events_observations_constraint_isolated_bad_first(client, evt_write_key, db):
    """CR7a: a caught DB error mid-batch leaves the connection usable for later events.

    Bad event first (savepoint rollback), good event second — proves partial-success
    is independent of failure *position* (not just good-then-bad).
    """
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)

    r = await _post_org_events(
        client,
        raw,
        org_id,
        [
            {"event_type_slug": "renamed", "event_month": 6},  # month w/o year → DB check
            {"event_type_slug": "founded", "event_year": 1995},
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "rejected"
    assert results[0]["reason"] == "invalid"
    assert results[1]["disposition"] == "new"
    n = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded' AND ee.event_year=1995""",
        org_id,
    )
    assert n == 1


async def test_org_events_observations_refine_constraint_isolated(client, evt_write_key, db):
    """CR7b: a DB constraint tripped on the refine (UPDATE) path is isolated too.

    A pm_event_id refine adding event_month with no year to a non-requires_year
    event (renamed) trips chk_month_requires_year on UPDATE → rejected/invalid,
    while a sibling good event in the same batch still lands.
    """
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    # seed a dateless `renamed` event (requires_year=False) to refine against
    r0 = await _post_org_events(client, raw, org_id, [{"event_type_slug": "renamed"}])
    renamed_id = r0.json()["results"][0]["event_id"]

    r = await _post_org_events(
        client,
        raw,
        org_id,
        [
            {"event_type_slug": "renamed", "pm_event_id": renamed_id, "event_month": 6},
            {"event_type_slug": "founded", "event_year": 1988},
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "rejected"
    assert results[0]["reason"] == "invalid"
    assert results[1]["disposition"] == "new"
    # the refine did not land — the renamed row is still dateless
    month = await db.fetchval("SELECT event_month FROM entity_events WHERE id=$1", renamed_id)
    assert month is None
    n = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded' AND ee.event_year=1988""",
        org_id,
    )
    assert n == 1


async def test_pm_event_id_refine_cannot_clear_required_year(client, evt_write_key, db):
    """CR #2: a refine that clears a required year → rejected/missing_required_field."""
    raw, _ = evt_write_key
    value, _org_id, event_id = await _org_with_founded(client, raw, db, 2013)

    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            # founded requires_year; omit event_year → merged year None
            "events": [{"event_type_slug": "founded", "pm_event_id": event_id}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] == "missing_required_field"
    yr = await db.fetchval("SELECT event_year FROM entity_events WHERE id=$1", event_id)
    assert yr == 2013


async def test_org_events_observations_unknown_org_404(client, evt_write_key):
    raw, _ = evt_write_key
    r = _post_org_events(
        client,
        raw,
        "01NONEXISTENTORGID00000000",
        [{"event_type_slug": "founded", "event_year": 1990}],
    )
    r = await r
    assert r.status_code == 404


async def test_org_events_observations_scope_enforcement(client, evt_read_key):
    r = await _post_org_events(
        client,
        evt_read_key,
        "01SOMEORGID000000000000000",
        [{"event_type_slug": "founded", "event_year": 1990}],
    )
    assert r.status_code == 403


async def test_event_place_address_id_written_to_db(client, evt_write_key, db):
    """Valid city-precision address → event row has event_place_address_id set."""
    raw, _ = evt_write_key
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, city, region, country, standardized, precision)"
        " VALUES ($1, 'Portland OR', 'Portland', 'OR', 'US', '1 Main St, Portland, OR', 'city')",
        aid,
    )
    value = _unique_id()
    try:
        r = await _post_people(
            client,
            raw,
            {
                "identifier_type": "person_wa_pdc",
                "identifier_value": value,
                "events": [
                    {
                        "event_type_slug": "birth",
                        "event_year": 1980,
                        "event_place_address_id": aid,
                    }
                ],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["disposition"] != "rejected"
        eid = body["entity_id"]

        row = await db.fetchrow(
            "SELECT event_place_address_id FROM entity_events WHERE entity_id=$1",
            eid,
        )
        assert row is not None
        assert row["event_place_address_id"] == aid
    finally:
        await db.execute("DELETE FROM addresses WHERE id=$1", aid)


# ---------------------------------------------------------------------------
# #322 — event void/retract for dateless linked events (close the correction loop)
# ---------------------------------------------------------------------------


async def _seed_succeeded_by(client, raw, db):
    """Anchor a successor + a `succeeded_by` link on a fresh predecessor.

    Returns (predecessor_id, successor_id, event_id).
    """
    rs = await _post_orgs(
        client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()}
    )
    successor_id = rs.json()["entity_id"]
    pred_id = await _make_org(client, raw, db)
    r = await _post_org_events(
        client,
        raw,
        pred_id,
        [
            {
                "event_type_slug": "succeeded_by",
                "linked_entity_type": "organization",
                "linked_entity_id": successor_id,
            }
        ],
    )
    event_id = r.json()["results"][0]["event_id"]
    return pred_id, successor_id, event_id


async def _outbox_count(db, entity_id):
    """entity_changes rows for an org — the BIGSERIAL outbox is a true
    in-transaction observable (unlike now(), which is transaction-constant under
    the rollback client, so updated_at can't distinguish a bump from a no-op)."""
    return await db.fetchval(
        "SELECT COUNT(*) FROM entity_changes WHERE entity_id=$1 AND entity_type='organization'",
        entity_id,
    )


async def test_event_retract_archives_dateless_linked_event(client, evt_write_key, db):
    """`op=retract` on a dateless `succeeded_by` → retracted + archived_at set + outbox emit."""
    raw, _ = evt_write_key
    pred_id, successor_id, event_id = await _seed_succeeded_by(client, raw, db)

    before = await _outbox_count(db, pred_id)

    r = await _post_org_events(
        client,
        raw,
        pred_id,
        [
            {
                "event_type_slug": "succeeded_by",
                "pm_event_id": event_id,
                "op": "retract",
                "linked_entity_type": "organization",
                "linked_entity_id": successor_id,
            }
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["disposition"] == "retracted"

    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is not None
    # the archive is an UPDATE → org-touch trigger → new outbox row (subscriber drops the anchor)
    assert await _outbox_count(db, pred_id) > before


async def test_event_retract_re_emit_is_noop_no_outbox(client, evt_write_key, db):
    """Re-retracting an already-archived event → auto-attached, no new outbox row.

    usa-wa re-emits every cycle; once retracted it keeps sending the retract. The
    second one must skip the UPDATE (no producer↔PM ping-pong) — proven by the
    outbox count holding steady.
    """
    raw, _ = evt_write_key
    pred_id, successor_id, event_id = await _seed_succeeded_by(client, raw, db)

    retract = {
        "event_type_slug": "succeeded_by",
        "pm_event_id": event_id,
        "op": "retract",
    }
    r1 = await _post_org_events(client, raw, pred_id, [retract])
    assert r1.json()["results"][0]["disposition"] == "retracted"

    outbox_after_first = await _outbox_count(db, pred_id)

    r2 = await _post_org_events(client, raw, pred_id, [retract])
    assert r2.json()["results"][0]["disposition"] == "auto-attached"
    assert await _outbox_count(db, pred_id) == outbox_after_first  # no-op, no emit


async def test_event_retract_unknown_id_event_not_found(client, evt_write_key, db):
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    r = await _post_org_events(
        client,
        raw,
        org_id,
        [
            {
                "event_type_slug": "founded",
                "pm_event_id": "01NONEXISTENTEVT0000000000",
                "op": "retract",
            }
        ],
    )
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["disposition"] == "rejected"
    assert res["reason"] == "event_not_found"


async def test_event_retract_foreign_source_provenance_conflict(
    client, evt_write_key, evt_write_key2, db
):
    """A different key retracting a source-stamped event → provenance_conflict; not archived."""
    raw1, _ = evt_write_key
    raw2, _ = evt_write_key2
    pred_id, successor_id, event_id = await _seed_succeeded_by(client, raw1, db)

    r = await _post_org_events(
        client,
        raw2,
        pred_id,
        [{"event_type_slug": "succeeded_by", "pm_event_id": event_id, "op": "retract"}],
    )
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["disposition"] == "rejected"
    assert res["reason"] == "provenance_conflict"
    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is None


async def test_event_retract_type_mismatch_identity_immutable(client, evt_write_key, db):
    """`op=retract` with an event_type that doesn't match the stored row → identity_immutable."""
    raw, _ = evt_write_key
    pred_id, successor_id, event_id = await _seed_succeeded_by(client, raw, db)

    r = await _post_org_events(
        client,
        raw,
        pred_id,
        # event_id is a succeeded_by, but caller claims it's `founded`
        [{"event_type_slug": "founded", "pm_event_id": event_id, "op": "retract"}],
    )
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["disposition"] == "rejected"
    assert res["reason"] == "identity_immutable"
    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is None


async def test_event_retract_without_pm_event_id_invalid(client, evt_write_key, db):
    """`op=retract` is always id-addressed; without pm_event_id → invalid."""
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    r = await _post_org_events(
        client,
        raw,
        org_id,
        [{"event_type_slug": "founded", "op": "retract"}],
    )
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["disposition"] == "rejected"
    assert res["reason"] == "invalid"


async def test_correction_loop_relink_new_plus_retract_old(client, evt_write_key, db):
    """End-to-end #322: re-link = create-new + retract-old in one partial-success batch.

    A mis-linked `succeeded_by` is corrected by emitting the right successor link
    and retracting the wrong one — both land, no admin intervention.
    """
    raw, _ = evt_write_key
    pred_id, wrong_successor, old_event_id = await _seed_succeeded_by(client, raw, db)
    # anchor the correct successor
    rc = await _post_orgs(
        client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()}
    )
    right_successor = rc.json()["entity_id"]

    r = await _post_org_events(
        client,
        raw,
        pred_id,
        [
            {
                "event_type_slug": "succeeded_by",
                "linked_entity_type": "organization",
                "linked_entity_id": right_successor,
            },
            {"event_type_slug": "succeeded_by", "pm_event_id": old_event_id, "op": "retract"},
        ],
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["disposition"] == "new"
    assert results[1]["disposition"] == "retracted"

    # exactly one active succeeded_by remains, pointing at the correct successor
    rows = await db.fetch(
        """SELECT linked_entity_id FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='succeeded_by' AND ee.archived_at IS NULL""",
        pred_id,
    )
    assert len(rows) == 1
    assert rows[0]["linked_entity_id"] == right_successor


async def test_event_retract_embedded_path(client, evt_write_key, db):
    """Retract also works embedded in an org observation payload (all-or-nothing)."""
    raw, _ = evt_write_key
    # seed a founded event via the embedded path so we have the org identifier
    value = _unique_id()
    r0 = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "event_year": 1990}],
        },
    )
    event_id = r0.json()["events"][0]["event_id"]

    r = await _post_orgs(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "events": [{"event_type_slug": "founded", "pm_event_id": event_id, "op": "retract"}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["disposition"] == "retracted"
    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is not None


# ---------------------------------------------------------------------------
# #322 CR round 1 — dedup skips archived; retract linked_entity guard; datable retract
# ---------------------------------------------------------------------------


async def test_reobserve_after_retract_stays_retracted(client, evt_write_key, db):
    """CR round 2: re-observing content identical to a retracted event does NOT
    resurrect it — the retract is authoritative (anti-resurrection, consistent
    with the address dateless-reobservation rule). The content-dedup matches the
    archived row → auto-attached, no fresh active row, event stays archived.
    """
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    # create a datable event, then retract it
    r1 = await _post_org_events(
        client, raw, org_id, [{"event_type_slug": "founded", "event_year": 1990}]
    )
    old_id = r1.json()["results"][0]["event_id"]
    r2 = await _post_org_events(
        client,
        raw,
        org_id,
        [{"event_type_slug": "founded", "pm_event_id": old_id, "op": "retract"}],
    )
    assert r2.json()["results"][0]["disposition"] == "retracted"

    # re-observe the SAME content — dedups against the archived row, no resurrect
    r3 = await _post_org_events(
        client, raw, org_id, [{"event_type_slug": "founded", "event_year": 1990}]
    )
    res = r3.json()["results"][0]
    assert res["disposition"] == "auto-attached"
    assert res["event_id"] == old_id
    # no active founded event exists — the retract stuck
    active = await db.fetch(
        """SELECT ee.id FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded' AND ee.archived_at IS NULL""",
        org_id,
    )
    assert active == []
    # and still exactly one (archived) row — re-observation minted nothing
    total = await db.fetchval(
        """SELECT COUNT(*) FROM entity_events ee
           JOIN entity_event_types t ON t.id = ee.event_type_id
           WHERE ee.entity_id=$1 AND t.slug='founded'""",
        org_id,
    )
    assert total == 1


async def test_event_retract_mismatched_linked_entity_identity_immutable(client, evt_write_key, db):
    """CR3: a retract supplying a linked_entity that doesn't match the stored row
    → identity_immutable (symmetric with the refine guard). Guards a copy-paste
    pm_event_id that happens to share event_type but points at a different link."""
    raw, _ = evt_write_key
    pred_id, successor_id, event_id = await _seed_succeeded_by(client, raw, db)
    # anchor a different org to name as the (wrong) linked entity
    ro = await _post_orgs(
        client, raw, {"identifier_type": "org_ubi", "identifier_value": _unique_id()}
    )
    other_id = ro.json()["entity_id"]

    r = await _post_org_events(
        client,
        raw,
        pred_id,
        [
            {
                "event_type_slug": "succeeded_by",
                "pm_event_id": event_id,
                "op": "retract",
                "linked_entity_type": "organization",
                "linked_entity_id": other_id,  # != the stored successor
            }
        ],
    )
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["disposition"] == "rejected"
    assert res["reason"] == "identity_immutable"
    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is None


async def test_event_retract_datable_event_native(client, evt_write_key, db):
    """CR4: the event-native surface retracts a datable non-linked event too
    (the op is general, not gated to dateless links)."""
    raw, _ = evt_write_key
    org_id = await _make_org(client, raw, db)
    r1 = await _post_org_events(
        client, raw, org_id, [{"event_type_slug": "dissolved", "event_year": 2020}]
    )
    event_id = r1.json()["results"][0]["event_id"]

    r = await _post_org_events(
        client,
        raw,
        org_id,
        [{"event_type_slug": "dissolved", "pm_event_id": event_id, "op": "retract"}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["disposition"] == "retracted"
    archived = await db.fetchval("SELECT archived_at FROM entity_events WHERE id=$1", event_id)
    assert archived is not None
