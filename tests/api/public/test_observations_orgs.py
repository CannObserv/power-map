"""Integration tests for POST /api/v1/orgs/observations."""

import hashlib
import os
from datetime import date

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


async def test_observation_stores_effective_dates(client, org_write_key, db):
    """A name observation carrying effective dates persists them on the new row (#239)."""
    raw, _ = org_write_key
    value = _unique_id()
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "names": [
                {
                    "name": "Committee on Old Government",
                    "name_type": "former",
                    "effective_start": "2019-01-01",
                    "effective_end": "2023-01-09",
                }
            ],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]
    row = await db.fetchrow(
        "SELECT effective_start, effective_end FROM organization_names"
        " WHERE organization_id=$1 AND name='Committee on Old Government'",
        eid,
    )
    assert row is not None
    assert row["effective_start"] == date(2019, 1, 1)
    assert row["effective_end"] == date(2023, 1, 9)


async def test_observation_reversed_effective_dates_rejected(client, org_write_key):
    """effective_start > effective_end is rejected at the request boundary (422), not silently."""
    raw, _ = org_write_key
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "names": [
                {
                    "name": "Backwards Interval",
                    "name_type": "former",
                    "effective_start": "2023-01-09",
                    "effective_end": "2019-01-01",
                }
            ],
        },
    )
    assert r.status_code == 422


async def test_org_acronym_created(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "org_acronyms": [{"acronym": "TSO", "is_canonical": False}],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE organization_id=$1 AND acronym='TSO'",
        eid,
    )
    assert row is not None
    # first acronym is auto-promoted to canonical when no is_canonical=True hint is given
    assert row["is_canonical"] is True


async def test_org_acronym_no_duplicate(client, org_write_key, db):
    raw, _ = org_write_key
    value = _unique_id()
    payload = {
        "identifier_type": "org_ubi",
        "identifier_value": value,
        "org_acronyms": [{"acronym": "NDO", "is_canonical": False}],
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


async def test_org_acronym_explicit_canonical_hint(client, org_write_key, db):
    """Explicit is_canonical=True on one acronym → only that one is canonical; others are not."""
    raw, _ = org_write_key
    value = _unique_id()
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "org_acronyms": [
                {"acronym": "PRIMARY", "is_canonical": True},
                {"acronym": "SECONDARY", "is_canonical": False},
            ],
        },
    )
    assert r.status_code == 200
    eid = r.json()["entity_id"]

    q = "SELECT is_canonical FROM organization_acronyms WHERE organization_id=$1 AND acronym=$2"
    primary = await db.fetchrow(q, eid, "PRIMARY")
    secondary = await db.fetchrow(q, eid, "SECONDARY")
    assert primary is not None and primary["is_canonical"] is True
    assert secondary is not None and secondary["is_canonical"] is False


async def test_org_second_acronym_not_canonical(client, org_write_key, db):
    """Second acronym added after canonical already exists stays non-canonical."""
    raw, _ = org_write_key
    value = _unique_id()
    # First observation — auto-promotes "FIRST" to canonical.
    r1 = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "org_acronyms": [{"acronym": "FIRST", "is_canonical": False}],
        },
    )
    assert r1.status_code == 200
    eid = r1.json()["entity_id"]

    # Second observation — "SECOND" cannot claim canonical; one already exists.
    r2 = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": value,
            "org_acronyms": [{"acronym": "SECOND", "is_canonical": False}],
        },
    )
    assert r2.status_code == 200

    q = "SELECT is_canonical FROM organization_acronyms WHERE organization_id=$1 AND acronym=$2"
    first = await db.fetchrow(q, eid, "FIRST")
    second = await db.fetchrow(q, eid, "SECOND")
    assert first is not None and first["is_canonical"] is True
    assert second is not None and second["is_canonical"] is False


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


# ---------------------------------------------------------------------------
# jurisdiction_affiliations in observations
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def obs_jur_fixtures(db):
    """Seed a jurisdiction for use in observation write tests."""
    jtype_id = generate_id()
    jur_id = generate_id()
    await db.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1,$2,$3)",
        jtype_id,
        f"test-jtype-{jtype_id[:8]}",
        "State",
    )
    jur_slug = f"test-jur-obs-{jur_id[:8]}"
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jur_id,
        jur_slug,
        "Obs Test Jurisdiction",
        jtype_id,
    )
    yield {"jur_id": jur_id}
    await db.execute(
        "DELETE FROM organization_jurisdiction_affiliations WHERE jurisdiction_id=$1", jur_id
    )
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", jur_id)
    await db.execute("DELETE FROM jurisdiction_types WHERE id=$1", jtype_id)


async def test_observation_creates_jurisdiction_affiliation(
    client, org_write_key, db, obs_jur_fixtures
):
    jur_id = obs_jur_fixtures["jur_id"]
    raw_key, _ = org_write_key
    uid_val = _unique_id()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": "org_ubi",
            "identifier_value": uid_val,
            "jurisdiction_affiliations": [
                {"jurisdiction_id": jur_id, "affiliation_type_slug": "governing"}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"  # fresh unique identifier always creates
    entity_id = body["entity_id"]

    row = await db.fetchrow(
        """
        SELECT a.organization_id, t.slug
        FROM organization_jurisdiction_affiliations a
        JOIN organization_jurisdiction_affiliation_types t ON t.id = a.affiliation_type_id
        WHERE a.organization_id = $1 AND a.jurisdiction_id = $2
        """,
        entity_id,
        jur_id,
    )
    assert row is not None
    assert row["slug"] == "governing"


async def test_observation_invalid_jurisdiction_id_returns_rejected(client, org_write_key, db):
    raw_key, _ = org_write_key
    r = _post(
        client,
        raw_key,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "jurisdiction_affiliations": [
                {
                    "jurisdiction_id": "00000000000000000000000000",
                    "affiliation_type_slug": "governing",
                }
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_observation_invalid_affiliation_type_returns_rejected(
    client, org_write_key, db, obs_jur_fixtures
):
    jur_id = obs_jur_fixtures["jur_id"]
    raw_key, _ = org_write_key
    r = _post(
        client,
        raw_key,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "jurisdiction_affiliations": [
                {"jurisdiction_id": jur_id, "affiliation_type_slug": "nonexistent_type"}
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# PM-native identifier (#198)
# ---------------------------------------------------------------------------


async def test_pm_org_id_auto_attached_on_existing_org(client, org_write_key, db):
    """identifier_type=pm_org_id targets an existing org by PM ULID → auto-attached."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "pm_org_id", "identifier_value": org_id})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "auto-attached"
    assert body["entity_id"] == org_id
    assert body["entity_type"] == "organization"


async def test_pm_org_id_rejected_on_unknown_ulid(client, org_write_key):
    """identifier_type=pm_org_id with unknown ULID → rejected (never creates entity)."""
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "pm_org_id", "identifier_value": generate_id()})
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


async def test_pm_org_id_suppressed_in_detail_response(client, org_write_key, org_read_key, db):
    """pm_org_id identifier rows are not surfaced by GET /orgs/{id}."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    pm_type = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='pm_org_id'")
    assert pm_type is not None, "pm_org_id type not seeded — run apply_schema"
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        org_id,
        pm_type["id"],
        org_id,
    )

    r = client.get(f"/api/v1/orgs/{org_id}", headers={"X-API-Key": org_read_key})
    assert r.status_code == 200
    slugs = [i["type_slug"] for i in r.json()["identifiers"]]
    assert "pm_org_id" not in slugs


# ---------------------------------------------------------------------------
# #225 — WA legislature identifier types
# ---------------------------------------------------------------------------


async def test_org_wa_legislature_chamber_accepted(client, org_write_key):
    """org_wa_legislature_chamber must be registered and accept org observations."""
    raw, _ = org_write_key
    r = _post(
        client,
        raw,
        {"identifier_type": "org_wa_legislature_chamber", "identifier_value": "house"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] in ("new", "auto-attached")
    assert body["entity_type"] == "organization"


async def test_org_wa_legislature_accepted(client, org_write_key):
    """org_wa_legislature must be registered and accept org observations."""
    raw, _ = org_write_key
    r = _post(
        client,
        raw,
        {"identifier_type": "org_wa_legislature", "identifier_value": "usa_wa_legislature"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] in ("new", "auto-attached")
    assert body["entity_type"] == "organization"


# ---------------------------------------------------------------------------
# #225 — reason field on rejected observations
# ---------------------------------------------------------------------------


async def test_rejected_unknown_type_includes_reason(client, org_write_key):
    """Unknown identifier type rejection must include a reason string."""
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "zzz_nonexistent_xyz", "identifier_value": "v"})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None
    assert "zzz_nonexistent_xyz" in body["reason"]


async def test_rejected_wrong_entity_type_includes_reason(client, org_write_key):
    """Wrong-entity-type rejection must include a reason string."""
    raw, _ = org_write_key
    r = _post(client, raw, {"identifier_type": "person_wa_pdc", "identifier_value": "v"})
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None


async def test_rejected_unknown_additional_identifier_includes_reason(client, org_write_key):
    """Unknown additional identifier type must surface a reason."""
    raw, _ = org_write_key
    r = _post(
        client,
        raw,
        {
            "identifier_type": "org_ubi",
            "identifier_value": _unique_id(),
            "additional_identifiers": [
                {"identifier_type_slug": "zzz_bad_slug", "identifier_value": "x"}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["reason"] is not None
    assert "zzz_bad_slug" in body["reason"]


# ---------------------------------------------------------------------------
# #240 — active flag via the observation paradigm (orgs-only)
# ---------------------------------------------------------------------------


async def test_observation_sets_active_false(client, org_write_key, db):
    """active=False in an observation marks an existing org inactive."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    try:
        raw, _ = org_write_key
        r = _post(
            client,
            raw,
            {"identifier_type": "pm_org_id", "identifier_value": org_id, "active": False},
        )
        assert r.status_code == 200
        assert r.json()["disposition"] == "auto-attached"
        row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
        assert row["active"] is False
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


async def test_observation_sets_active_true_reactivates(client, org_write_key, db):
    """active=True in an observation reactivates a previously inactive org."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, FALSE)", org_id)
    try:
        raw, _ = org_write_key
        r = _post(
            client,
            raw,
            {"identifier_type": "pm_org_id", "identifier_value": org_id, "active": True},
        )
        assert r.status_code == 200
        assert r.json()["disposition"] == "auto-attached"
        row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
        assert row["active"] is True
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


async def test_observation_omitted_active_leaves_org_unchanged(client, org_write_key, db):
    """An observation without an active field must not touch the flag."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, FALSE)", org_id)
    try:
        raw, _ = org_write_key
        r = _post(client, raw, {"identifier_type": "pm_org_id", "identifier_value": org_id})
        assert r.status_code == 200
        assert r.json()["disposition"] == "auto-attached"
        row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
        assert row["active"] is False  # untouched
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


async def test_observation_active_on_archived_org_rejected(client, org_write_key, db):
    """Setting active on an archived org is a malformed observation → rejected."""
    org_id = generate_id()
    ubi_val = _unique_id()
    await db.execute("INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", org_id)
    ubi_type = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug='org_ubi'")
    assert ubi_type is not None, "org_ubi type not seeded — run apply_schema"
    eid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        eid,
        org_id,
        ubi_type["id"],
        ubi_val,
    )
    try:
        raw, _ = org_write_key
        r = _post(
            client,
            raw,
            {"identifier_type": "org_ubi", "identifier_value": ubi_val, "active": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["disposition"] == "rejected"
        assert body["reason"] is not None
        assert "archiv" in body["reason"].lower()
        # The flag must remain untouched on rejection.
        row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
        assert row["active"] is True
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", eid)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


async def test_observation_active_change_emits_entity_change(client, org_write_key, db):
    """An effective active toggle appends exactly one entity_changes 'updated' row."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    try:
        before = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
        raw, _ = org_write_key
        r = _post(
            client,
            raw,
            {"identifier_type": "pm_org_id", "identifier_value": org_id, "active": False},
        )
        assert r.status_code == 200
        rows = await db.fetch(
            "SELECT change_kind FROM entity_changes"
            " WHERE entity_id=$1 AND entity_type='organization' AND id > $2",
            org_id,
            before,
        )
        assert len(rows) == 1, f"expected 1 change row, got {len(rows)}"
        assert rows[0]["change_kind"] == "updated"
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


async def test_observation_active_noop_emits_no_entity_change(client, org_write_key, db):
    """A redundant active assertion (value unchanged) emits no entity_changes row."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, FALSE)", org_id)
    try:
        before = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
        raw, _ = org_write_key
        r = _post(
            client,
            raw,
            {"identifier_type": "pm_org_id", "identifier_value": org_id, "active": False},
        )
        assert r.status_code == 200
        rows = await db.fetch(
            "SELECT 1 FROM entity_changes WHERE entity_id=$1 AND id > $2",
            org_id,
            before,
        )
        assert len(rows) == 0, f"expected no change rows for a no-op, got {len(rows)}"
    finally:
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
