"""Integration tests for schema constraints.

Verifies that CHECK constraints, partial unique indexes, FK constraints,
and updated_at triggers all fire correctly against a live PostgreSQL instance.

Run with:
    DATABASE_URL=postgres://user:pass@localhost/power_map_test \\
        uv run pytest -m integration -v

The target database should be dedicated to testing. Schema is applied once
at session start by the `db_pool` fixture in `tests/conftest.py`
(`apply_schema()` is idempotent, so re-runs are safe). Each test acquires a
connection from `db_pool` via the `db` fixture and wraps its DML in a
transaction that is rolled back on teardown, leaving the schema intact and
the tables empty.
"""

import json

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.types import PERSON_NAME_TYPES

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


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
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
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


async def _link_type_id(conn: asyncpg.Connection, slug: str = "website") -> str:
    """Return the id of the seeded link_type for *slug*."""
    row = await conn.fetchrow("SELECT id FROM link_types WHERE slug = $1", slug)
    assert row is not None, f"link_type slug {slug!r} not found — check seed data in schema.sql"
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
# organization_names: uq_org_canonical_name (one canonical per org, all types)
# ---------------------------------------------------------------------------


async def test_duplicate_canonical_org_name_rejected(db):
    """Two is_canonical=TRUE names for the same org must be rejected."""
    org_id = await _org(db)  # already inserts one canonical legal name

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, $3, 'dba', TRUE)",
                generate_id(),
                org_id,
                "Duplicate Canonical Name",
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
                "nickname",  # not in ('legal', 'dba', 'former')
            )


async def test_org_name_acronym_type_rejected(db):
    """'acronym' is no longer a valid name_type in organization_names."""
    org_id = await _org(db)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type)"
                " VALUES ($1, $2, $3, 'acronym')",
                generate_id(),
                org_id,
                "ACME",
            )


async def test_org_name_valid_name_types_accepted(db):
    """All three valid name_type values must be accepted."""
    org_id = await _org(db)

    for name_type in ("legal", "dba", "former"):
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
# organization_acronyms: uq_org_canonical_acronym
# ---------------------------------------------------------------------------


async def test_org_acronym_insert(db):
    """Inserting an acronym into organization_acronyms must succeed."""
    org_id = await _org(db)

    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        org_id,
        "ACME",
    )


async def test_org_acronym_duplicate_canonical_rejected(db):
    """Two is_canonical=TRUE acronyms for the same org must be rejected."""
    org_id = await _org(db)

    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        org_id,
        "ACME",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                org_id,
                "ACM",
            )


async def test_org_multiple_noncanonical_acronyms_accepted(db):
    """Multiple is_canonical=FALSE acronyms for the same org must be allowed."""
    org_id = await _org(db)

    for acronym in ("ACME", "ACM", "AC"):
        await db.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, FALSE)",
            generate_id(),
            org_id,
            acronym,
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
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug = 'org_ubi'")

    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '603-123-456')",
        generate_id(),
        org_id,
        row["id"],
    )


async def test_wa_legislature_identifier_types_seeded(db):
    """Both WA Legislature identifier type slugs must exist with correct entity_type."""
    rows = await db.fetch(
        "SELECT slug, entity_type FROM entity_identifier_types WHERE slug = ANY($1)",
        ["person_wa_legislature_member_id", "org_wa_legislature_committee_id"],
    )
    assert {r["slug"]: r["entity_type"] for r in rows} == {
        "person_wa_legislature_member_id": "person",
        "org_wa_legislature_committee_id": "organization",
    }


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
    row = await db.fetchrow("SELECT updated_at FROM organizations WHERE id = $1", org_id)
    assert row["updated_at"].year > 2000, "Trigger did not override the explicit updated_at value"


# Per-binding coverage for trg_updated_at_<table>. Each insert helper inserts
# one row and returns its id; the test then UPDATEs that row with an explicit
# updated_at far in the past and asserts the trigger overrode it. A regression
# in any single table's binding (e.g. the trigger getting dropped) makes that
# table's parametrized case fail in isolation.


async def _insert_address(conn: asyncpg.Connection) -> str:
    aid = generate_id()
    # Pass country explicitly: older test DBs may lack the schema default.
    await conn.execute(
        "INSERT INTO addresses (id, raw_input, country) VALUES ($1, $2, $3)",
        aid,
        "123 Test St",
        "US",
    )
    return aid


async def _insert_people(conn: asyncpg.Connection) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _insert_role(conn: asyncpg.Connection) -> str:
    org_id = await _org(conn)
    return await _role(conn, org_id)


async def _insert_role_assignment(conn: asyncpg.Connection) -> str:
    org_id = await _org(conn)
    person_id = await _person(conn)
    role_id = await _role(conn, org_id)
    ra_id = generate_id()
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        ra_id,
        person_id,
        role_id,
    )
    return ra_id


async def _insert_app_user(conn: asyncpg.Connection) -> str:
    uid = generate_id()
    await conn.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)",
        uid,
        "test@example.com",
    )
    return uid


@pytest.mark.parametrize(
    ("table", "insert_helper"),
    [
        ("addresses", _insert_address),
        ("people", _insert_people),
        ("roles", _insert_role),
        ("role_assignments", _insert_role_assignment),
        ("app_users", _insert_app_user),
    ],
)
async def test_updated_at_trigger_binding_per_table(db, table, insert_helper):
    """Every trg_updated_at_<table> binding must override an explicit updated_at.

    Mirrors test_updated_at_trigger_overrides_explicit_value (organizations) and
    test_person_name_parts_updated_at_trigger_overrides_explicit_value
    (person_name_parts). Adding a new table with an updated_at trigger should
    drop in as a new (table, insert_helper) tuple here.
    """
    row_id = await insert_helper(db)
    await db.execute(
        f"UPDATE {table} SET updated_at = '2000-01-01' WHERE id = $1",
        row_id,
    )
    row = await db.fetchrow(f"SELECT updated_at FROM {table} WHERE id = $1", row_id)
    assert row["updated_at"].year > 2000, (
        f"trg_updated_at_{table} did not override the explicit updated_at value"
    )


# ---------------------------------------------------------------------------
# organizations: chk_no_self_parent
# ---------------------------------------------------------------------------


async def test_org_self_parent_rejected(db):
    """Setting an org's parent_id to itself must be rejected.

    The BEFORE trigger fires before the CHECK constraint, so we get a
    RaiseError (cycle detected) rather than a CheckViolationError.
    """
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    with pytest.raises(asyncpg.exceptions.RaiseError):
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
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", b_id, a_id)
    c_id = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", c_id, b_id)
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
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", b_id, a_id)
    c_id = await _org(db)
    # Re-parent b under c — no cycle
    await db.execute("UPDATE organizations SET parent_id = $1 WHERE id = $2", c_id, b_id)


# ---------------------------------------------------------------------------
# person_names: name_type CHECK
# ---------------------------------------------------------------------------


async def test_person_name_invalid_name_type_rejected(db):
    """An unrecognized name_type value must violate the CHECK."""
    person_id = await _person(db)

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO person_names (id, person_id, name, name_type) VALUES ($1, $2, $3, $4)",
                generate_id(),
                person_id,
                "Bad Name",
                "nickname",  # not in src.core.types.PERSON_NAME_TYPES
            )


async def test_person_name_valid_name_types_accepted(db):
    """Every value in PERSON_NAME_TYPES must be accepted by the CHECK.

    Drives off the constant so future schema additions surface here
    automatically (parity with the schema is enforced separately by
    ``tests/core/test_types.py``).
    """
    person_id = await _person(db)

    for name_type in PERSON_NAME_TYPES:
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type) VALUES ($1, $2, $3, $4)",
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
                "nonexistent_type",  # not in the CHECK constraint's allowed values
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
# links: entity_type CHECK
# ---------------------------------------------------------------------------


async def test_link_invalid_entity_type_rejected(db):
    """An unrecognized entity_type must violate the CHECK."""
    org_id = await _org(db)
    link_type_id = await _link_type_id(db, "twitter")

    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO links"
                " (id, entity_type, entity_id, link_type_id, url)"
                " VALUES ($1, $2, $3, $4, 'https://twitter.com/test')",
                generate_id(),
                "invalid_type",  # not in ('organization', 'person', 'role', 'role_assignment')
                org_id,
                link_type_id,
            )


async def test_link_valid_entity_types_accepted(db):
    """All four valid entity_type values must be accepted."""
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
    link_type_id = await _link_type_id(db, "twitter")

    for entity_type, entity_id in (
        ("organization", org_id),
        ("person", person_id),
        ("role", role_id),
        ("role_assignment", ra_id),
    ):
        await db.execute(
            "INSERT INTO links"
            " (id, entity_type, entity_id, link_type_id, url)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            entity_type,
            entity_id,
            link_type_id,
            f"https://twitter.com/{entity_type}",
        )


# ---------------------------------------------------------------------------
# import_batches
# ---------------------------------------------------------------------------


async def test_import_batches_insert(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches
               (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id,
        "orgs.csv",
        "abc123",
        10,
        9,
        1,
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
        """INSERT INTO import_batches
               (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id,
        "orgs.csv",
        "abc123",
        1,
        1,
        0,
    )
    prov_id = generate_id()
    org_id = generate_id()
    await db.execute(
        """INSERT INTO import_provenance
               (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        prov_id,
        batch_id,
        2,
        "organization",
        org_id,
        "created",
        json.dumps({"Name": "Acme Corp"}),
    )
    row = await db.fetchrow("SELECT * FROM import_provenance WHERE id = $1", prov_id)
    assert row["action"] == "created"
    assert row["entity_type"] == "organization"


async def test_import_provenance_invalid_action(db):
    batch_id = generate_id()
    await db.execute(
        """INSERT INTO import_batches
               (id, source_file, file_hash, row_count, loaded_count, error_count)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        batch_id,
        "orgs.csv",
        "abc123",
        1,
        0,
        1,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await db.execute(
                """INSERT INTO import_provenance
                       (id, batch_id, source_row, entity_type, entity_id, action, raw_data)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                generate_id(),
                batch_id,
                1,
                "organization",
                generate_id(),
                "bogus",
                "{}",
            )


# ---------------------------------------------------------------------------
# field_confidence
# ---------------------------------------------------------------------------


async def test_field_confidence_insert(db):
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    conf_id = generate_id()
    await db.execute(
        """INSERT INTO field_confidence
               (id, entity_type, entity_id, field_name, value_hash,
                source_reliability, validation_status)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        conf_id,
        "organization",
        org_id,
        "phone",
        "abc123hash",
        0.8,
        "unconfirmed",
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
                generate_id(),
                "organization",
                org_id,
                "phone",
                "abc123",
                1.5,
                "unconfirmed",  # out of range
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
            generate_id(),
            "organization",
            org_id,
            "phone",
            "samehash",
            0.8,
            "unconfirmed",
        )
    count = await db.fetchval("SELECT count(*) FROM field_confidence WHERE entity_id = $1", org_id)
    assert count == 2


async def test_google_drive_link_type_seeded(db):
    row = await db.fetchrow("SELECT * FROM link_types WHERE slug = 'google_drive'")
    assert row is not None
    assert row["display_name"] == "Google Drive"
    assert row["is_social"] is False


async def test_legislative_district_type_seeded(db):
    """Generic legislative_district type must exist (shared upper/lower boundary states like WA)."""
    row = await db.fetchrow(
        "SELECT id, slug, display_name FROM jurisdiction_types WHERE slug = 'legislative_district'"
    )
    assert row is not None, "jurisdiction_type 'legislative_district' not seeded — check schema.sql"
    assert row["display_name"] == "Legislative District"


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
                1,
                "organization",
                generate_id(),
                "created",
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
                generate_id(),
                "organization",
                org_id,
                "phone",
                "abc123",
                -0.1,
                "unconfirmed",  # below 0.0
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
                generate_id(),
                "organization",
                org_id,
                "phone",
                # "bogus" not in ('confirmed', 'unconfirmed', 'failed', 'not_attempted')
                "abc123",
                0.8,
                "bogus",
            )


# ---------------------------------------------------------------------------
# archived_at columns
# ---------------------------------------------------------------------------


async def test_organizations_has_archived_at(db):
    """organizations.archived_at column must exist and be nullable."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    row = await db.fetchrow("SELECT archived_at FROM organizations WHERE id = $1", org_id)
    assert row["archived_at"] is not None


async def test_people_has_archived_at(db):
    person_id = await _person(db)
    await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)
    row = await db.fetchrow("SELECT archived_at FROM people WHERE id = $1", person_id)
    assert row["archived_at"] is not None


async def test_roles_has_archived_at(db):
    org_id = await _org(db)
    role_id = await _role(db, org_id)
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    row = await db.fetchrow("SELECT archived_at FROM roles WHERE id = $1", role_id)
    assert row["archived_at"] is not None


async def test_role_assignments_has_archived_at(db):
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
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    row = await db.fetchrow("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id)
    assert row["archived_at"] is not None


# ---------------------------------------------------------------------------
# roles: uq_role_org_title
# These tests require the index to exist. apply_schema() skips index creation
# if duplicate rows are present; run scripts/deduplicate_roles.py first.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def require_uq_role_org_title(db):
    """Skip tests that need uq_role_org_title if it hasn't been created yet."""
    exists = await db.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = 'uq_role_org_title'")
    if not exists:
        pytest.skip(
            "uq_role_org_title index not present — "
            "run scripts/deduplicate_roles.py --execute then re-apply schema"
        )


async def test_duplicate_role_same_org_title_rejected(db, require_uq_role_org_title):
    """Two active roles with the same org+title must be rejected."""
    org_id = await _org(db)
    await _role(db, org_id)  # inserts role with title "Test Role"

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                generate_id(),
                org_id,
                "Test Role",
            )


async def test_role_title_case_insensitive_dedup(db, require_uq_role_org_title):
    """Same title in different case under the same org must be rejected."""
    org_id = await _org(db)
    await _role(db, org_id)  # title "Test Role"

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                generate_id(),
                org_id,
                "TEST ROLE",
            )


async def test_archived_role_does_not_block_same_title(db, require_uq_role_org_title):
    """Archived role must not block a new active role with the same org+title."""
    org_id = await _org(db)
    role_id = await _role(db, org_id)
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)

    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        generate_id(),
        org_id,
        "Test Role",
    )


async def test_same_title_different_orgs_allowed(db, require_uq_role_org_title):
    """Same title under different orgs must be allowed."""
    org_a = await _org(db)
    org_b = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_b)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        org_b,
        "Org B",
    )

    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        generate_id(),
        org_a,
        "Director",
    )
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        generate_id(),
        org_b,
        "Director",
    )


# ---------------------------------------------------------------------------
# role_assignments: uq_role_assignment_person_role_start (NULLS NOT DISTINCT)
# ---------------------------------------------------------------------------


async def test_duplicate_assignment_null_start_date_rejected(db):
    """Two assignments with same person+role+NULL start_date must be rejected."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        generate_id(),
        person_id,
        role_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
                generate_id(),
                person_id,
                role_id,
            )


async def test_assignment_different_start_dates_allowed(db):
    """Same person+role with different start_dates is allowed (role held twice)."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)

    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
        " VALUES ($1, $2, $3, '2020-01-01')",
        generate_id(),
        person_id,
        role_id,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
        " VALUES ($1, $2, $3, '2023-01-01')",
        generate_id(),
        person_id,
        role_id,
    )


async def test_archived_assignment_does_not_block_same_start_date(db):
    """Archived assignment must not block a new active assignment with the same key."""
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
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)

    # New active assignment with same person+role+NULL start_date must succeed
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        generate_id(),
        person_id,
        role_id,
    )


# ---------------------------------------------------------------------------
# organization_jurisdiction_affiliations
# ---------------------------------------------------------------------------


async def _jur_type(conn: asyncpg.Connection) -> str:
    # No explicit cleanup — db fixture rolls back every test via tr.rollback().
    jtid = generate_id()
    await conn.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name) VALUES ($1, $2, $3)",
        jtid,
        f"test-jtype-{jtid[:8]}",
        "Test Type",
    )
    return jtid


async def _jurisdiction(conn: asyncpg.Connection, jur_type_id: str) -> str:
    # No explicit cleanup — db fixture rolls back every test via tr.rollback().
    jid = generate_id()
    await conn.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
        jid,
        f"test-jur-{jid[:8]}",
        "Test Jurisdiction",
        jur_type_id,
    )
    return jid


async def _affiliation_type_id(conn: asyncpg.Connection, slug: str = "governing") -> str:
    row = await conn.fetchrow(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug = $1", slug
    )
    assert row is not None, f"affiliation_type '{slug}' not seeded — check schema.sql"
    return row["id"]


async def test_affiliation_type_governing_seeded(db):
    row = await db.fetchrow(
        "SELECT id, slug, display_name"
        " FROM organization_jurisdiction_affiliation_types WHERE slug = 'governing'"
    )
    assert row is not None
    assert row["display_name"] == "is governed by"


async def test_affiliation_type_registered_seeded(db):
    row = await db.fetchrow(
        "SELECT id, slug, display_name"
        " FROM organization_jurisdiction_affiliation_types WHERE slug = 'registered'"
    )
    assert row is not None
    assert row["display_name"] == "is registered in"


async def test_affiliation_insert_valid(db):
    org_id = await _org(db)
    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    at_id = await _affiliation_type_id(db)
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        org_id,
        jur_id,
        at_id,
    )
    count = await db.fetchval(
        "SELECT count(*) FROM organization_jurisdiction_affiliations WHERE organization_id = $1",
        org_id,
    )
    assert count == 1


async def test_affiliation_invalid_org_rejected(db):
    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    at_id = await _affiliation_type_id(db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_jurisdiction_affiliations"
                " (id, organization_id, jurisdiction_id, affiliation_type_id)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                "00000000000000000000000000",
                jur_id,
                at_id,
            )


async def test_affiliation_invalid_jurisdiction_rejected(db):
    org_id = await _org(db)
    at_id = await _affiliation_type_id(db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_jurisdiction_affiliations"
                " (id, organization_id, jurisdiction_id, affiliation_type_id)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                org_id,
                "00000000000000000000000000",
                at_id,
            )


async def test_affiliation_invalid_type_rejected(db):
    org_id = await _org(db)
    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_jurisdiction_affiliations"
                " (id, organization_id, jurisdiction_id, affiliation_type_id)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                org_id,
                jur_id,
                "00000000000000000000000000",
            )


async def test_affiliation_duplicate_triple_rejected(db):
    org_id = await _org(db)
    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    at_id = await _affiliation_type_id(db)
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        org_id,
        jur_id,
        at_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_jurisdiction_affiliations"
                " (id, organization_id, jurisdiction_id, affiliation_type_id)"
                " VALUES ($1, $2, $3, $4)",
                generate_id(),
                org_id,
                jur_id,
                at_id,
            )


async def test_affiliation_same_org_two_types_accepted(db):
    org_id = await _org(db)
    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    governing_id = await _affiliation_type_id(db, "governing")
    registered_id = await _affiliation_type_id(db, "registered")
    for at_id in (governing_id, registered_id):
        await db.execute(
            "INSERT INTO organization_jurisdiction_affiliations"
            " (id, organization_id, jurisdiction_id, affiliation_type_id)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            org_id,
            jur_id,
            at_id,
        )
    count = await db.fetchval(
        "SELECT count(*) FROM organization_jurisdiction_affiliations WHERE organization_id = $1",
        org_id,
    )
    assert count == 2


async def test_affiliation_insert_touches_org_updated_at(db):
    # INSERT with explicitly old updated_at — BEFORE UPDATE trigger doesn't fire on INSERT,
    # so the sentinel sticks. Affiliation INSERT then fires touch_parent_org (AFTER INSERT),
    # which UPDATEs organizations → set_updated_at (BEFORE UPDATE) advances it to NOW().
    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, updated_at) VALUES ($1, '2000-01-01'::timestamptz)",
        org_id,
    )
    old = await db.fetchval("SELECT updated_at FROM organizations WHERE id = $1", org_id)
    assert old.year == 2000

    jt_id = await _jur_type(db)
    jur_id = await _jurisdiction(db, jt_id)
    at_id = await _affiliation_type_id(db)
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        org_id,
        jur_id,
        at_id,
    )
    new = await db.fetchval("SELECT updated_at FROM organizations WHERE id = $1", org_id)
    assert new.year > 2000


async def test_affiliation_trigger_covers_delete_event(db):
    """Trigger must be registered for DELETE events (tgtype bit 8 per pg_trigger)."""
    row = await db.fetchrow(
        """
        SELECT tgname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'organization_jurisdiction_affiliations'
          AND t.tgname = 'trg_touch_org_on_affiliation_change'
          AND (t.tgtype & 8) > 0
          AND t.tgenabled != 'D'
        """
    )
    assert row is not None, "trg_touch_org_on_affiliation_change not registered for DELETE"
