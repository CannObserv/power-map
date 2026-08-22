"""Seed INSERTs survive an operator-created row holding a seeded slug (#458).

Every seeded lookup table pairs a ULID ``id`` PK with a UNIQUE natural key
(``slug``), and every seed block conflicts on the PK only. The admin Settings
screen mints identifier types and link types with an operator-supplied slug and
a *fresh* ULID, so a release that later seeds that slug collides on the slug,
not on the id: ``ON CONFLICT (id)`` never fires and the statement raises
``unique_violation``.

That abort is not survivable. ``apply-schema.sh`` is ``ExecStartPre=`` on the
API unit, so a failed apply means the service does not start — mid-deploy, on a
release CI called green, triggered by state that exists only in production.

``reconcile_seeded_slugs()`` runs ahead of each seed and re-ids the operator's
row onto the seeded ULID, re-pointing FK children first, so identifier values
already attached to it survive. Deleting the row is not an option: real data
hangs off it.

Non-transactional on purpose — these call ``apply_schema`` the way production
does. Each test restores the rows it moved.
"""

import pytest

from src.core.db import apply_schema, generate_id

pytestmark = [
    pytest.mark.integration,
]

# A seeded pair with no admin create path of its own, so nothing in the suite
# mints a competing row for it.
SEEDED_ID = "01KKZ3WGJSZF0F96SMYC000AVT"
SEEDED_SLUG = "person_ssn"

_INSERT_TYPE = """
INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name)
VALUES ($1, 'person', $2, 'SSN', 'Operator-created Social Security Number')
"""

_INSERT_IDENTIFIER = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, '123-45-6789')
"""


async def test_apply_schema_reids_operator_created_slug(db_pool):
    """The operator's row is re-idd onto the seeded ULID; its data survives."""
    operator_id = generate_id()
    identifier_id = generate_id()

    async with db_pool.acquire() as conn:
        try:
            # The release has not seeded this slug yet: the seeded row does not
            # exist, and an operator hand-created the type to unblock a consumer.
            await conn.execute("DELETE FROM entity_identifier_types WHERE id = $1", SEEDED_ID)
            await conn.execute(_INSERT_TYPE, operator_id, SEEDED_SLUG)
            await conn.execute(_INSERT_IDENTIFIER, identifier_id, generate_id(), operator_id)

            await apply_schema(conn)

            holders = await conn.fetch(
                "SELECT id FROM entity_identifier_types WHERE slug = $1", SEEDED_SLUG
            )
            assert [r["id"] for r in holders] == [SEEDED_ID]
            assert (
                await conn.fetchval(
                    "SELECT entity_identifier_type_id FROM identifiers WHERE id = $1",
                    identifier_id,
                )
                == SEEDED_ID
            )
            # The seed's payload wins over the operator's placeholder wording.
            assert (
                await conn.fetchval(
                    "SELECT full_name FROM entity_identifier_types WHERE id = $1",
                    SEEDED_ID,
                )
                == "United States Social Security Number"
            )
        finally:
            await conn.execute("DELETE FROM identifiers WHERE id = $1", identifier_id)
            await conn.execute("DELETE FROM entity_identifier_types WHERE id = $1", operator_id)


async def test_apply_schema_parks_slug_when_seeded_id_is_taken(db_pool):
    """A seeded slug *rename* onto an occupied slug parks the occupant aside.

    The re-id has nowhere to land — the seeded ULID is already in use by the row
    whose slug this release renames — so the collider keeps its id, its children
    and its payload, and only its slug moves out of the way. An operator merges
    the two afterwards; the apply itself must not abort.
    """
    operator_id = generate_id()
    identifier_id = generate_id()

    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "UPDATE entity_identifier_types SET slug = $2 WHERE id = $1",
                SEEDED_ID,
                f"{SEEDED_SLUG}_pre_rename",
            )
            await conn.execute(_INSERT_TYPE, operator_id, SEEDED_SLUG)
            await conn.execute(_INSERT_IDENTIFIER, identifier_id, generate_id(), operator_id)

            await apply_schema(conn)

            assert (
                await conn.fetchval(
                    "SELECT slug FROM entity_identifier_types WHERE id = $1", SEEDED_ID
                )
                == SEEDED_SLUG
            )
            assert (
                await conn.fetchval(
                    "SELECT slug FROM entity_identifier_types WHERE id = $1",
                    operator_id,
                )
                == f"{SEEDED_SLUG}_superseded_{operator_id.lower()}"
            )
            assert (
                await conn.fetchval(
                    "SELECT entity_identifier_type_id FROM identifiers WHERE id = $1",
                    identifier_id,
                )
                == operator_id
            )
        finally:
            await conn.execute("DELETE FROM identifiers WHERE id = $1", identifier_id)
            await conn.execute("DELETE FROM entity_identifier_types WHERE id = $1", operator_id)
            # apply_schema restores the seeded slug itself; this only matters
            # when the assertions above never got that far.
            await conn.execute(
                "UPDATE entity_identifier_types SET slug = $2 WHERE id = $1",
                SEEDED_ID,
                SEEDED_SLUG,
            )


# Every seeded lookup that pairs a ULID PK with a UNIQUE slug, with the extra
# NOT NULL columns a synthetic row needs.
RECONCILED_TABLES = [
    ("role_assignment_relationship_types", {}),
    ("link_types", {}),
    ("entity_identifier_types", {"entity_type": "person", "full_name": "Probe"}),
    ("jurisdiction_types", {}),
    ("jurisdiction_relationship_types", {"category": "spatial"}),
    ("role_types", {}),
    ("entity_event_types", {"applies_to": "person"}),
    ("organization_jurisdiction_affiliation_types", {}),
]


@pytest.mark.parametrize(("table", "extra"), RECONCILED_TABLES)
async def test_reconcile_seeded_slugs_reids_every_seeded_lookup(db_pool, table, extra):
    """The helper works against each table the seed section routes through it."""
    probe_slug = "zz_probe_458"
    operator_id = generate_id()
    seeded_id = generate_id()

    columns = ["id", "slug", "display_name", *extra]
    values = [operator_id, probe_slug, "Probe", *extra.values()]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))

    async with db_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                *values,
            )
            await conn.execute(f"CREATE TEMP TABLE _probe_seed (LIKE {table} INCLUDING DEFAULTS)")
            values[0] = seeded_id
            await conn.execute(
                f"INSERT INTO _probe_seed ({', '.join(columns)}) VALUES ({placeholders})",
                *values,
            )

            moved = await conn.fetchval(
                "SELECT reconcile_seeded_slugs($1::regclass, '_probe_seed'::regclass)",
                table,
            )

            assert moved == 1
            assert (
                await conn.fetchval(f"SELECT id FROM {table} WHERE slug = $1", probe_slug)
                == seeded_id
            )
        finally:
            await tx.rollback()
