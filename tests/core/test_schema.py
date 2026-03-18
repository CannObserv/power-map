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
    """Two is_canonical=TRUE names for the same org must be rejected."""
    org_id = await _org(db)  # already inserts one canonical name

    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
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
                "nickname",  # not in ('legal', 'former', 'preferred', 'alias')
            )


async def test_person_name_valid_name_types_accepted(db):
    """All four valid name_type values must be accepted."""
    person_id = await _person(db)

    for name_type in ("legal", "former", "preferred", "alias"):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            person_id,
            f"Name ({name_type})",
            name_type,
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
