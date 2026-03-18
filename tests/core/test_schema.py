"""Integration tests for schema constraints.

Verifies that CHECK constraints, partial unique indexes, FK constraints,
and updated_at triggers all fire correctly against a live PostgreSQL instance.

Run with:
    DATABASE_URL=postgres://user:pass@localhost/power_map_test \\
        uv run pytest -m integration -v

The target database should be dedicated to testing. apply_schema() is
idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING), so re-runs are safe.
Each test wraps its DML in a transaction that is rolled back on teardown,
leaving the schema intact but the tables empty.
"""

import json
import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Connect, apply schema, yield a connection whose DML rolls back after."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Helpers: insert prerequisite rows
# ---------------------------------------------------------------------------


async def _org(conn: asyncpg.Connection) -> str:
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        "Test Org",
    )
    return oid


async def _person(conn: asyncpg.Connection) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        "Test Person",
    )
    return pid


async def _role(conn: asyncpg.Connection, org_id: str) -> str:
    rid = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org_id,
        "Test Role",
    )
    return rid


async def _url_type_id(conn: asyncpg.Connection, slug: str = "website") -> str:
    """Return the id of the seeded url_type for *slug*."""
    row = await conn.fetchrow("SELECT id FROM url_types WHERE slug = $1", slug)
    assert row is not None, f"url_type slug {slug!r} not found — check seed data in schema.sql"
    return row["id"]


async def _platform_id(conn: asyncpg.Connection, slug: str = "twitter") -> str:
    """Return the id of the seeded platform for *slug*."""
    row = await conn.fetchrow("SELECT id FROM platforms WHERE slug = $1", slug)
    assert row is not None, f"platform slug {slug!r} not found — check seed data in schema.sql"
    return row["id"]


# ---------------------------------------------------------------------------
# role_assignments: chk_current_no_end_date
# ---------------------------------------------------------------------------


async def test_role_assignment_current_with_end_date_rejected(db):
    """is_current=TRUE and a non-null end_date must violate the CHECK."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():  # savepoint; rolls back on exception
            await db.execute(
                "INSERT INTO role_assignments"
                " (id, person_id, role_id, is_current, end_date)"
                " VALUES ($1, $2, $3, TRUE, '2024-06-30')",
                generate_id(),
                person_id,
                role_id,
            )


async def test_role_assignment_current_null_end_date_accepted(db):
    """is_current=TRUE with end_date=NULL must succeed."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        role_id,
    )


async def test_role_assignment_former_with_end_date_accepted(db):
    """is_current=FALSE with an end_date (known exit) must succeed."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    await db.execute(
        "INSERT INTO role_assignments"
        " (id, person_id, role_id, is_current, end_date)"
        " VALUES ($1, $2, $3, FALSE, '2023-12-31')",
        generate_id(),
        person_id,
        role_id,
    )


async def test_role_assignment_duplicate_current_rejected(db):
    """Two current assignments for the same (person, role) must be rejected."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        role_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                person_id,
                role_id,
            )


# ---------------------------------------------------------------------------
# organization_names: uq_org_canonical_name
# ---------------------------------------------------------------------------


async def test_duplicate_canonical_org_name_rejected(db):
    """Two is_canonical=TRUE legal names for the same org must be rejected."""
    org_id = await _org(db)  # already inserts one canonical legal name

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, $3, 'legal', TRUE)",
                generate_id(),
                org_id,
                "Duplicate Canonical Legal Name",
            )


async def test_multiple_noncanonical_org_names_accepted(db):
    """Multiple is_canonical=FALSE names for the same org must be allowed."""
    org_id = await _org(db)

    for alias in ("Alias One", "Alias Two", "Alias Three"):
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, FALSE)",
            generate_id(),
            org_id,
            alias,
        )


async def test_canonical_uniqueness_per_name_type(db):
    """One canonical per (org, name_type): legal + acronym may each be canonical."""
    org_id = await _org(db)  # inserts canonical legal name

    # Canonical acronym alongside canonical legal should succeed
    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'acronym', TRUE)",
        generate_id(),
        org_id,
        "ACME",
    )


async def test_duplicate_canonical_same_type_rejected(db):
    """Two is_canonical=TRUE rows with the same (org, name_type) must be rejected."""
    org_id = await _org(db)

    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'acronym', TRUE)",
        generate_id(),
        org_id,
        "ACME",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, $3, 'acronym', TRUE)",
                generate_id(),
                org_id,
                "ACM",
            )


async def test_org_name_invalid_name_type_rejected(db):
    """An unrecognized name_type value must violate the CHECK."""
    org_id = await _org(db)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                org_id,
                "Bad Name",
                "nickname",  # not in ('legal', 'dba', 'former', 'acronym')
            )


async def test_org_name_valid_name_types_accepted(db):
    """All four valid name_type values must be accepted."""
    org_id = await _org(db)

    for name_type in ("legal", "dba", "former", "acronym"):
        await db.execute(
            "INSERT INTO organization_names"
            " (id, organization_id, name, name_type)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            org_id,
            f"Name ({name_type})",
            name_type,
        )


# ---------------------------------------------------------------------------
# urls: uq_url_canonical
# ---------------------------------------------------------------------------


async def test_duplicate_canonical_url_rejected(db):
    """Two is_canonical=TRUE urls for the same entity must be rejected."""
    org_id = await _org(db)
    url_type_id = await _url_type_id(db, "website")

    await db.execute(
        "INSERT INTO urls (id, entity_type, entity_id, url, url_type_id, is_canonical)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        generate_id(),
        org_id,
        url_type_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO urls"
                " (id, entity_type, entity_id, url, url_type_id, is_canonical)"
                " VALUES ($1, 'organization', $2, 'https://other.com', $3, TRUE)",
                generate_id(),
                org_id,
                url_type_id,
            )


# ---------------------------------------------------------------------------
# identifiers: FK to entity_identifier_types
# ---------------------------------------------------------------------------


async def test_identifier_with_nonexistent_type_rejected(db):
    """Inserting an identifier with an unknown entity_identifier_type_id must fail."""
    org_id = await _org(db)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                " VALUES ($1, $2, $3, '12345')",
                generate_id(),
                org_id,
                "00000000000000000000000000",  # non-existent ULID
            )


async def test_identifier_with_valid_type_accepted(db):
    """Inserting an identifier with a seeded entity_identifier_type_id must succeed."""
    org_id = await _org(db)
    row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug = 'org_ubi'"
    )

    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '603-123-456')",
        generate_id(),
        org_id,
        row["id"],
    )


# ---------------------------------------------------------------------------
# updated_at trigger
# ---------------------------------------------------------------------------


async def test_updated_at_trigger_overrides_explicit_value(db):
    """Trigger must overwrite an explicit updated_at supplied in an UPDATE."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    # Attempt to set updated_at to a value far in the past; trigger should override.
    await db.execute(
        "UPDATE organizations SET active = FALSE, updated_at = '2000-01-01' WHERE id = $1",
        org_id,
    )
    row = await db.fetchrow(
        "SELECT updated_at FROM organizations WHERE id = $1", org_id
    )
    assert row["updated_at"].year > 2000, (
        "Trigger did not override the explicit updated_at value"
    )


# ---------------------------------------------------------------------------
# organizations: chk_no_self_parent
# ---------------------------------------------------------------------------


async def test_org_self_parent_rejected(db):
    """Setting an org's parent_id to itself must violate the CHECK."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "UPDATE organizations SET parent_id = $1 WHERE id = $1",
                org_id,
            )


async def test_org_parent_hierarchy_accepted(db):
    """A valid parent → child org relationship must be accepted."""
    parent_id = await _org(db)
    child_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id) VALUES ($1, $2)",
        child_id,
        parent_id,
    )


# ---------------------------------------------------------------------------
# organizations: cycle prevention trigger
# ---------------------------------------------------------------------------


async def test_org_cycle_two_node_rejected(db):
    """A → B → A cycle must be rejected by the trigger."""
    a_id = await _org(db)
    b_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id) VALUES ($1, $2)",
        b_id,
        a_id,
    )
    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with db.transaction():
            await db.execute(
                "UPDATE organizations SET parent_id = $1 WHERE id = $2",
                b_id,
                a_id,
            )


async def test_org_cycle_three_node_rejected(db):
    """A → B → C → A (three-node cycle) must be rejected by the trigger."""
    a_id = await _org(db)
    b_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", b_id, a_id
    )
    c_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", c_id, b_id
    )
    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with db.transaction():
            await db.execute(
                "UPDATE organizations SET parent_id = $1 WHERE id = $2",
                c_id,
                a_id,
            )


async def test_org_reparent_no_cycle_accepted(db):
    """Moving a child to a different parent (no cycle) must be accepted."""
    a_id = await _org(db)
    b_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", b_id, a_id
    )
    c_id = await _org(db)
    # Re-parent b under c — no cycle
    await db.execute(
        "UPDATE organizations SET parent_id = $1 WHERE id = $2", c_id, b_id
    )


# ---------------------------------------------------------------------------
# person_names: name_type CHECK
# ---------------------------------------------------------------------------


async def test_person_name_invalid_name_type_rejected(db):
    """An unrecognized name_type value must violate the CHECK."""
    person_id = await _person(db)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO person_names (id, person_id, name, name_type)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                person_id,
                "Bad Name",
                "nickname",  # not in ('legal', 'former', 'preferred', 'alias', 'initials')
            )


async def test_person_name_valid_name_types_accepted(db):
    """All five valid name_type values must be accepted."""
    person_id = await _person(db)

    for name_type in ("legal", "former", "preferred", "alias", "initials"):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            person_id,
            f"Name ({name_type})",
            name_type,
        )


async def test_duplicate_canonical_person_name_rejected(db):
    """Two is_canonical=TRUE legal names for the same person must be rejected."""
    person_id = await _person(db)  # already inserts one canonical legal name

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, $3, 'legal', TRUE)",
                generate_id(),
                person_id,
                "Duplicate Canonical Legal Name",
            )


async def test_canonical_person_name_uniqueness_per_name_type(db):
    """One canonical per (person, name_type): legal + initials may each be canonical."""
    person_id = await _person(db)  # inserts canonical legal name

    # Canonical initials alongside canonical legal should succeed
    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'initials', TRUE)",
        generate_id(),
        person_id,
        "A.B.C.",
    )


async def test_duplicate_canonical_person_name_same_type_rejected(db):
    """Two is_canonical=TRUE rows with the same (person, name_type) must be rejected."""
    person_id = await _person(db)

    await db.execute(
        "INSERT INTO person_names"
        " (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'initials', TRUE)",
        generate_id(),
        person_id,
        "A.B.C.",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, $3, 'initials', TRUE)",
                generate_id(),
                person_id,
                "X.Y.Z.",
            )


# ---------------------------------------------------------------------------
# contact_methods: entity_type CHECK
# ---------------------------------------------------------------------------


async def test_contact_method_invalid_entity_type_rejected(db):
    """An unrecognized entity_type must violate the CHECK."""
    org_id = await _org(db)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO contact_methods"
                " (id, entity_type, entity_id, contact_type, value)"
                " VALUES ($1, $2, $3, 'email', 'x@example.com')",
                generate_id(),
                "role",  # not in ('organization', 'person', 'role_assignment')
                org_id,
            )


async def test_contact_method_valid_entity_types_accepted(db):
    """All three valid entity_type values must be accepted."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        ra_id,
        person_id,
        role_id,
    )

    for entity_type, entity_id in (
        ("organization", org_id),
        ("person", person_id),
        ("role_assignment", ra_id),
    ):
        await db.execute(
            "INSERT INTO contact_methods"
            " (id, entity_type, entity_id, contact_type, value)"
            " VALUES ($1, $2, $3, 'phone', '+12065551234')",
            generate_id(),
            entity_type,
            entity_id,
        )


# ---------------------------------------------------------------------------
# social_links: entity_type CHECK
# ---------------------------------------------------------------------------


async def test_social_link_invalid_entity_type_rejected(db):
    """An unrecognized entity_type must violate the CHECK."""
    org_id = await _org(db)
    platform_id = await _platform_id(db, "twitter")

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO social_links"
                " (id, entity_type, entity_id, platform_id, url)"
                " VALUES ($1, $2, $3, $4, 'https://twitter.com/test')",
                generate_id(),
                "role",  # not in ('organization', 'person', 'role_assignment')
                org_id,
                platform_id,
            )


async def test_social_link_valid_entity_types_accepted(db):
    """All three valid entity_type values must be accepted."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        ra_id,
        person_id,
        role_id,
    )
    platform_id = await _platform_id(db, "twitter")

    for entity_type, entity_id in (
        ("organization", org_id),
        ("person", person_id),
        ("role_assignment", ra_id),
    ):
        await db.execute(
            "INSERT INTO social_links"
            " (id, entity_type, entity_id, platform_id, url)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            entity_type,
            entity_id,
            platform_id,
            f"https://twitter.com/{entity_type}",
        )


# ---------------------------------------------------------------------------
# import_batches
# ---------------------------------------------------------------------------


async def test_import_batches_insert(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 10, 9, 1,
    )
    row = await db.fetchrow("SELECT * FROM import_batches WHERE id = $1", batch_id)
    assert row["row_count"] == 10
    assert row["error_count"] == 1


# ---------------------------------------------------------------------------
# import_provenance
# ---------------------------------------------------------------------------

async def test_import_provenance_insert(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 1, 1, 0,
    )
    prov_id = generate_id()
    org_id = generate_id()
    await db.execute(
        """INSERT INTO import_provenance
               (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        prov_id, batch_id, 2, "organization", org_id, "created",
        json.dumps({"Name": "Acme Corp"}),
    )
    row = await db.fetchrow("SELECT * FROM import_provenance WHERE id = $1", prov_id)
    assert row["action"] == "created"
    assert row["entity_type"] == "organization"


async def test_import_provenance_invalid_action(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id, "orgs.csv", "abc123", 1, 0, 1,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO import_provenance
                       (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(), batch_id, 1, "organization", generate_id(), "bogus",
                "{}",
            )


# ---------------------------------------------------------------------------
# field_confidence
# ---------------------------------------------------------------------------

async def test_field_confidence_insert(db):
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id) VALUES ($1)", org_id
    )
    conf_id = generate_id()
    await db.execute(
        """INSERT INTO field_confidence
               (id, entity_type, entity_id, field_name, value_hash,
                source_reliability, validation_status)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        conf_id, "organization", org_id, "phone",
        "abc123hash", 0.8, "unconfirmed",
    )
    row = await db.fetchrow("SELECT * FROM field_confidence WHERE id = $1", conf_id)
    assert row["source_reliability"] == pytest.approx(0.8)
    assert row["validation_status"] == "unconfirmed"


async def test_field_confidence_source_reliability_bounds(db):
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO field_confidence
                       (id, entity_type, entity_id, field_name, value_hash,
                        source_reliability, validation_status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(), "organization", org_id, "phone",
                "abc123", 1.5, "unconfirmed",  # out of range
            )


async def test_field_confidence_append_only_by_convention(db):
    """Two confidence rows for same entity+field is allowed (append-only history)."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    for _ in range(2):
        await db.execute(
            """INSERT INTO field_confidence
                   (id, entity_type, entity_id, field_name, value_hash,
                    source_reliability, validation_status)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            generate_id(), "organization", org_id, "phone",
            "samehash", 0.8, "unconfirmed",
        )
    count = await db.fetchval(
        "SELECT count(*) FROM field_confidence WHERE entity_id = $1", org_id
    )
    assert count == 2


async def test_url_type_google_drive_seeded(db):
    row = await db.fetchrow("SELECT * FROM url_types WHERE slug = 'google_drive'")
    assert row is not None
    assert row["display_name"] == "Google Drive"


# ---------------------------------------------------------------------------
# import_provenance: FK constraint
# ---------------------------------------------------------------------------


async def test_import_provenance_nonexistent_batch_id_rejected(db):
    """Inserting import_provenance with a nonexistent batch_id must violate FK."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO import_provenance
                       (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(),
                "00000000000000000000000000",  # non-existent batch_id
                1, "organization", generate_id(), "created",
                "{}",
            )


# ---------------------------------------------------------------------------
# field_confidence: source_reliability lower bound
# ---------------------------------------------------------------------------


async def test_field_confidence_source_reliability_lower_bound(db):
    """source_reliability below 0.0 must violate the CHECK."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO field_confidence
                       (id, entity_type, entity_id, field_name, value_hash,
                        source_reliability, validation_status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(), "organization", org_id, "phone",
                "abc123", -0.1, "unconfirmed",  # below 0.0
            )


# ---------------------------------------------------------------------------
# field_confidence: invalid validation_status
# ---------------------------------------------------------------------------


async def test_field_confidence_invalid_validation_status_rejected(db):
    """An unrecognized validation_status must violate the CHECK."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO field_confidence
                       (id, entity_type, entity_id, field_name, value_hash,
                        source_reliability, validation_status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(), "organization", org_id, "phone",
                "abc123", 0.8, "bogus",  # not in ('confirmed', 'unconfirmed', 'failed', 'not_attempted')
            )
