"""Comprehensive integration tests for POST /api/v1/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# Auth / key fixtures (mirrors test_observations_route.py with distinct data)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def obs_scope_behav(db):
    """Ensure api_key_scope_types includes observations:write."""
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id = $1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    yield scope_id


@pytest_asyncio.fixture(loop_scope="session")
async def write_key(db, obs_scope_behav):
    """API key with observations:write; yields (raw_key, key_id)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "obs_behav@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Obs Behav Key",
        raw_key[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)",
        kid,
        obs_scope_behav,
    )
    yield raw_key, kid

    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def no_scope_key(db, obs_scope_behav):
    """API key without observations:write; yields raw_key."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "obs_noscope_behav@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Obs No-Scope",
        raw_key[:8],
        key_hash,
    )
    yield raw_key

    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# ---------------------------------------------------------------------------
# Entity-identifier-type fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def person_eit_slug(db):
    """Ensure a person-type entity_identifier_type exists; yield its slug."""
    slug = "person_wa_pdc"
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not row:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'person', $2, 'WA PDC', 'Washington State PDC Person')",
            eit_id,
            slug,
        )
    yield slug


@pytest_asyncio.fixture(loop_scope="session")
async def org_eit_slug(db):
    """Ensure an org-type entity_identifier_type exists; yield its slug."""
    slug = "org_ubi"
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not row:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'organization', $2, 'UBI', 'Washington Unified Business Identifier')",
            eit_id,
            slug,
        )
    yield slug


@pytest_asyncio.fixture(loop_scope="session")
async def additional_eit_slug(db):
    """Ensure a second person-type entity_identifier_type exists for additional-identifier tests."""
    slug = "person_obs_test_secondary"
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not row:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'person', $2, 'Obs Secondary', 'Observation Test Secondary ID')",
            eit_id,
            slug,
        )
    yield slug


@pytest_asyncio.fixture(loop_scope="session")
async def link_type_slug(db):
    """Return an existing link_type slug from the DB (seeded by schema)."""
    row = await db.fetchrow("SELECT slug FROM link_types LIMIT 1")
    assert row, "No link_types rows — schema seed not applied"
    return row["slug"]


@pytest_asyncio.fixture(loop_scope="session")
async def real_role_id(db):
    """Create a minimal org + role for role_assignment tests; yield role_id."""
    org_id = generate_id()
    role_id = generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        role_id,
        org_id,
    )
    yield role_id

    await db.execute("DELETE FROM role_assignments WHERE role_id=$1", role_id)
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(client, raw_key, payload):
    """POST /api/v1/observations with the given payload and API key."""
    return client.post(
        "/api/v1/observations",
        json=payload,
        headers={"X-API-Key": raw_key},
    )


def _unique_value():
    """Random identifier value for each test invocation."""
    return "behav_" + os.urandom(6).hex()


# ---------------------------------------------------------------------------
# 1. Disposition: new
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_disposition_new(client, write_key, person_eit_slug):
    """Fresh identifier → disposition='new', non-null entity_id."""
    raw_key, _ = write_key
    r = _post(
        client,
        raw_key,
        {"identifier_type": person_eit_slug, "identifier_value": _unique_value()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "new"
    assert body["entity_id"] is not None
    assert body["entity_type"] == "person"


# ---------------------------------------------------------------------------
# 2. Disposition: auto-attached
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_disposition_auto_attached(client, write_key, org_eit_slug):
    """Same identifier submitted twice → second call returns auto-attached and same entity_id."""
    raw_key, _ = write_key
    value = _unique_value()
    payload = {"identifier_type": org_eit_slug, "identifier_value": value}

    r1 = _post(client, raw_key, payload)
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["disposition"] == "new"
    first_entity_id = body1["entity_id"]

    r2 = _post(client, raw_key, payload)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["disposition"] == "auto-attached"
    assert body2["entity_id"] == first_entity_id


# ---------------------------------------------------------------------------
# 3. Disposition: rejected — unknown identifier_type
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_disposition_rejected_unknown_type(client, write_key):
    """Unknown identifier_type → disposition='rejected', entity_id=None."""
    raw_key, _ = write_key
    r = _post(
        client,
        raw_key,
        {"identifier_type": "zzz_nonexistent_type_xyz", "identifier_value": "anything"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["entity_id"] is None
    assert body["entity_type"] is None


# ---------------------------------------------------------------------------
# 4. Scope enforcement
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_no_scope_returns_403(client, no_scope_key):
    """Key without observations:write → 403."""
    r = _post(
        client,
        no_scope_key,
        {"identifier_type": "org_ubi", "identifier_value": "anything"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 5. Name append
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_name_append_creates_row(client, write_key, person_eit_slug, db):
    """Submitting a name claim creates a person_names row with visibility='public'."""
    raw_key, key_id = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "names": [{"name": "Jane Doe", "name_type": "legal"}],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT visibility, source_key_id FROM person_names WHERE person_id=$1 AND name='Jane Doe'",
        entity_id,
    )
    assert row is not None, "person_names row should exist"
    assert row["visibility"] == "public"
    assert row["source_key_id"] == key_id


@pytest.mark.integration
async def test_name_append_no_duplicate(client, write_key, person_eit_slug, db):
    """Submitting same name twice does not create a duplicate row."""
    raw_key, _ = write_key
    value = _unique_value()
    payload = {
        "identifier_type": person_eit_slug,
        "identifier_value": value,
        "names": [{"name": "John Smith", "name_type": "legal"}],
    }

    r1 = _post(client, raw_key, payload)
    assert r1.status_code == 200
    entity_id = r1.json()["entity_id"]

    r2 = _post(client, raw_key, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM person_names WHERE person_id=$1 AND name='John Smith'",
        entity_id,
    )
    assert count == 1, "Duplicate name row must not be created"


# ---------------------------------------------------------------------------
# 6. Name parts
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_name_parts_created(client, write_key, person_eit_slug, db):
    """Submitting names[].parts creates a person_name_parts row."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "names": [
                {
                    "name": "María García",
                    "name_type": "legal",
                    "parts": {
                        "given_names": ["María"],
                        "family_names": ["García"],
                        "primary_identifier": "family",
                    },
                }
            ],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    name_row = await db.fetchrow(
        "SELECT id FROM person_names WHERE person_id=$1 AND name='María García'",
        entity_id,
    )
    assert name_row, "person_names row should exist"

    parts_row = await db.fetchrow(
        "SELECT given_names, family_names, primary_identifier"
        " FROM person_name_parts WHERE person_name_id=$1",
        name_row["id"],
    )
    assert parts_row is not None, "person_name_parts row should exist"
    assert "María" in parts_row["given_names"]
    assert "García" in parts_row["family_names"]
    assert parts_row["primary_identifier"] == "family"


# ---------------------------------------------------------------------------
# 7. Link append
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_link_append_creates_row(client, write_key, org_eit_slug, link_type_slug, db):
    """Submitting a link claim creates a row in links."""
    raw_key, _ = write_key
    value = _unique_value()
    url = f"https://example.com/{value}"

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": org_eit_slug,
            "identifier_value": value,
            "links": [{"url": url, "link_type_slug": link_type_slug}],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT id FROM links WHERE entity_id=$1 AND url=$2",
        entity_id,
        url,
    )
    assert row is not None, "links row should exist"


@pytest.mark.integration
async def test_link_append_no_duplicate(client, write_key, org_eit_slug, link_type_slug, db):
    """Same link submitted twice → no duplicate row."""
    raw_key, _ = write_key
    value = _unique_value()
    url = f"https://example.com/{value}"
    payload = {
        "identifier_type": org_eit_slug,
        "identifier_value": value,
        "links": [{"url": url, "link_type_slug": link_type_slug}],
    }

    r1 = _post(client, raw_key, payload)
    assert r1.status_code == 200
    entity_id = r1.json()["entity_id"]

    r2 = _post(client, raw_key, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM links WHERE entity_id=$1 AND url=$2",
        entity_id,
        url,
    )
    assert count == 1, "Duplicate link row must not be created"


# ---------------------------------------------------------------------------
# 8. Contact method — valid email and invalid phone
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_contact_method_email_created(client, write_key, person_eit_slug, db):
    """Valid email contact method creates a contact_methods row."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "contact_methods": [{"contact_type": "email", "value": "test@example.com"}],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT contact_type FROM contact_methods WHERE entity_id=$1 AND contact_type='email'",
        entity_id,
    )
    assert row is not None, "contact_methods row should exist"


@pytest.mark.integration
async def test_invalid_phone_returns_rejected(client, write_key, person_eit_slug):
    """Malformed phone number → disposition='rejected'."""
    raw_key, _ = write_key
    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": _unique_value(),
            "contact_methods": [{"contact_type": "phone", "value": "not-a-phone-number!!!"}],
        },
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# 9. Org acronym
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_org_acronym_created(client, write_key, org_eit_slug, db):
    """Org observation with org_acronyms creates a non-canonical row in organization_acronyms."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": org_eit_slug,
            "identifier_value": value,
            "org_acronyms": ["TST"],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE organization_id=$1 AND acronym='TST'",
        entity_id,
    )
    assert row is not None, "organization_acronyms row should exist"
    assert row["is_canonical"] is False


@pytest.mark.integration
async def test_org_acronym_no_duplicate(client, write_key, org_eit_slug, db):
    """Same acronym submitted twice → no duplicate row."""
    raw_key, _ = write_key
    value = _unique_value()
    payload = {
        "identifier_type": org_eit_slug,
        "identifier_value": value,
        "org_acronyms": ["NDP"],
    }

    r1 = _post(client, raw_key, payload)
    assert r1.status_code == 200
    entity_id = r1.json()["entity_id"]

    r2 = _post(client, raw_key, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM organization_acronyms WHERE organization_id=$1 AND acronym='NDP'",
        entity_id,
    )
    assert count == 1, "Duplicate acronym row must not be created"


# ---------------------------------------------------------------------------
# 10. Role assignment
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_role_assignment_created(client, write_key, person_eit_slug, real_role_id, db):
    """Person observation with role_assignments creates a role_assignments row."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "role_assignments": [{"role_id": real_role_id}],
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        entity_id,
        real_role_id,
    )
    assert row is not None, "role_assignments row should exist"


@pytest.mark.integration
async def test_role_assignment_no_duplicate(client, write_key, person_eit_slug, real_role_id, db):
    """Second submission with same role → no duplicate row (open assignment already exists)."""
    raw_key, _ = write_key
    value = _unique_value()
    payload = {
        "identifier_type": person_eit_slug,
        "identifier_value": value,
        "role_assignments": [{"role_id": real_role_id}],
    }

    r1 = _post(client, raw_key, payload)
    assert r1.status_code == 200
    entity_id = r1.json()["entity_id"]

    r2 = _post(client, raw_key, payload)
    assert r2.status_code == 200

    count = await db.fetchval(
        "SELECT COUNT(*) FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND end_date IS NULL AND archived_at IS NULL",
        entity_id,
        real_role_id,
    )
    assert count == 1, "Duplicate open role_assignment must not be created"


# ---------------------------------------------------------------------------
# 11. Pronouns — write-if-null
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pronouns_set(client, write_key, person_eit_slug, db):
    """Submitting personal_pronouns on a new person sets the field."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "personal_pronouns": "they/them",
        },
    )
    assert r.status_code == 200
    entity_id = r.json()["entity_id"]

    row = await db.fetchrow(
        "SELECT personal_pronouns FROM people WHERE id=$1",
        entity_id,
    )
    assert row["personal_pronouns"] == "they/them"


@pytest.mark.integration
async def test_pronouns_write_if_null(client, write_key, person_eit_slug, db):
    """Second submission with different pronouns → original value unchanged (write-if-null)."""
    raw_key, _ = write_key
    value = _unique_value()

    r1 = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "personal_pronouns": "she/her",
        },
    )
    assert r1.status_code == 200
    entity_id = r1.json()["entity_id"]

    r2 = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "personal_pronouns": "he/him",
        },
    )
    assert r2.status_code == 200

    row = await db.fetchrow("SELECT personal_pronouns FROM people WHERE id=$1", entity_id)
    assert row["personal_pronouns"] == "she/her", "write-if-null: original value must be preserved"


# ---------------------------------------------------------------------------
# 12. Org parent by ID
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_org_parent_by_id(client, write_key, org_eit_slug, db):
    """Submitting organization_parent_id sets organizations.parent_id."""
    raw_key, _ = write_key

    # Create a parent org via observation
    parent_value = _unique_value()
    r_parent = _post(
        client,
        raw_key,
        {"identifier_type": org_eit_slug, "identifier_value": parent_value},
    )
    assert r_parent.status_code == 200
    parent_entity_id = r_parent.json()["entity_id"]

    # Create child org and set parent
    child_value = _unique_value()
    r_child = _post(
        client,
        raw_key,
        {
            "identifier_type": org_eit_slug,
            "identifier_value": child_value,
            "organization_parent_id": parent_entity_id,
        },
    )
    assert r_child.status_code == 200
    child_entity_id = r_child.json()["entity_id"]

    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", child_entity_id)
    assert row["parent_id"] == parent_entity_id


# ---------------------------------------------------------------------------
# 13. Org parent by name — ambiguous → rejected
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_org_parent_by_name_ambiguous_rejected(client, write_key, db):
    """Two orgs with same canonical name → organization_parent_name lookup → rejected."""
    raw_key, key_id = write_key
    shared_name = "Ambiguous Corp " + _unique_value()

    # Directly insert two orgs with the same canonical name
    org_id_a = generate_id()
    org_id_b = generate_id()
    name_a_id = generate_id()
    name_b_id = generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id_a)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id_b)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical,"
        " source_key_id) VALUES ($1, $2, $3, 'legal', TRUE, $4)",
        name_a_id,
        org_id_a,
        shared_name,
        key_id,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical,"
        " source_key_id) VALUES ($1, $2, $3, 'legal', TRUE, $4)",
        name_b_id,
        org_id_b,
        shared_name,
        key_id,
    )

    try:
        # Submit a new org observation referencing the ambiguous parent name
        r = _post(
            client,
            raw_key,
            {
                "identifier_type": "org_ubi",
                "identifier_value": _unique_value(),
                "organization_parent_name": shared_name,
            },
        )
        assert r.status_code == 200
        assert r.json()["disposition"] == "rejected"
    finally:
        await db.execute("DELETE FROM organization_names WHERE id=$1", name_a_id)
        await db.execute("DELETE FROM organization_names WHERE id=$1", name_b_id)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id_a)
        await db.execute("DELETE FROM organizations WHERE id=$1", org_id_b)


# ---------------------------------------------------------------------------
# 14. Additional identifiers
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_additional_identifier_creates_row(
    client, write_key, person_eit_slug, additional_eit_slug, db
):
    """Submitting additional_identifiers creates an identifiers row for the entity."""
    raw_key, _ = write_key
    value = _unique_value()

    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "additional_identifiers": [
                {"identifier_type_slug": additional_eit_slug, "identifier_value": "extra_" + value}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] in ("new", "auto-attached")
    entity_id = body["entity_id"]

    eit = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug=$1", additional_eit_slug
    )
    row = await db.fetchrow(
        "SELECT value FROM identifiers WHERE entity_id=$1 AND entity_identifier_type_id=$2",
        entity_id,
        eit["id"],
    )
    assert row is not None


@pytest.mark.integration
async def test_additional_identifier_conflict_rejected(
    client, write_key, person_eit_slug, additional_eit_slug, db
):
    """Same additional identifier type with different value → rejected."""
    raw_key, _ = write_key
    value = _unique_value()

    # First submission establishes the entity and the additional identifier
    r1 = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "additional_identifiers": [
                {"identifier_type_slug": additional_eit_slug, "identifier_value": "first_" + value}
            ],
        },
    )
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "new"

    # Second submission: same primary identifier, different value for the additional type → conflict
    r2 = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "additional_identifiers": [
                {
                    "identifier_type_slug": additional_eit_slug,
                    "identifier_value": "conflict_" + value,
                }
            ],
        },
    )
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "rejected"


# ---------------------------------------------------------------------------
# 15. Orphaned entity retry (resolver commits outside writer transaction)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_orphaned_entity_auto_attached_on_retry(client, write_key, person_eit_slug, db):
    """Failed write (bad phone) leaves entity; retry with valid data returns auto-attached."""
    raw_key, _ = write_key
    value = _unique_value()

    # First call: creates entity + identifier, but writer rejects due to bad phone
    r1 = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": value,
            "contact_methods": [{"contact_type": "phone", "value": "not-a-phone!!!"}],
        },
    )
    assert r1.status_code == 200
    assert r1.json()["disposition"] == "rejected"

    # Entity + identifier row should still exist (resolve_entity committed independently)
    eit = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", person_eit_slug)
    row = await db.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id=$1 AND value=$2",
        eit["id"],
        value,
    )
    assert row is not None, "Entity must persist even after rejected write"

    # Second call: same identifier, valid data → auto-attached
    r2 = _post(
        client,
        raw_key,
        {"identifier_type": person_eit_slug, "identifier_value": value},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["disposition"] == "auto-attached"
    assert body2["entity_id"] == row["entity_id"]


# ---------------------------------------------------------------------------
# 16. DB constraint violations return rejected (not 500)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_invalid_name_type_returns_rejected(client, write_key, person_eit_slug):
    """Submitting an invalid name_type is rejected at Pydantic validation (422)."""
    raw_key, _ = write_key
    r = _post(
        client,
        raw_key,
        {
            "identifier_type": person_eit_slug,
            "identifier_value": _unique_value(),
            "names": [{"name": "Jane Doe", "name_type": "not_a_valid_type"}],
        },
    )
    # Pydantic Literal enforcement → 422 Unprocessable Entity
    assert r.status_code == 422
