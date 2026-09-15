"""apply_schema restores inline CHECKs on pre-existing DBs (#307 CR, #312, #315).

``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so constraints
added inline to the CREATE never reach a DB whose table predates them — prod
was missing ``entity_events_event_year_check`` (and ``chk_at_requires_year``)
entirely while fresh DBs had both. The #312 sweep found five more in the same
state (``field_confidence``/``import_provenance`` entity_type checks +
``import_batches`` count checks): their only reconciliation was a
replace-if-stale ``IF EXISTS (... NOT LIKE ...)`` guard (or none at all), which
no-ops when the constraint is *entirely absent*. The companion DO blocks must
ADD each constraint when absent, not only replace an old clause.

The same no-op also masks *modifiers* added inline after the fact — an FK's
``ON DELETE`` action, not just its presence. #315: prod's
``entity_events_event_place_address_id_fkey`` was plain NO ACTION while the
inline ``REFERENCES … ON DELETE SET NULL`` never applied, so hard-deleting an
address referenced by an event errored in prod but nulled the ref on fresh DBs.
``test_apply_schema_repairs_fk_on_delete_action`` reproduces that drift shape
(constraint present, wrong ``confdeltype``) — distinct from the absence shape
above — and asserts apply_schema repairs the action.

Non-transactional on purpose: apply_schema's phase 2 runs CREATE INDEX
CONCURRENTLY, which cannot execute inside a transaction. Dropping a constraint
and re-running apply_schema restores the exact prior state, so the test is
self-cleaning.
"""

import pytest

from src.core.db import apply_schema, generate_id

pytestmark = [
    pytest.mark.integration,
]

_CONSTRAINT_DEF_SQL = """
SELECT pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = $1::regclass AND conname = $2
"""

_CONSTRAINT_COUNT_SQL = """
SELECT count(*)
FROM pg_constraint
WHERE conrelid = $1::regclass AND conname = $2
"""


@pytest.mark.parametrize(
    ("table", "conname", "expected_fragments"),
    [
        # #307 CR — entity_events.
        ("entity_events", "entity_events_event_year_check", ("event_year <> 0",)),
        ("entity_events", "chk_at_requires_year", ("event_at IS NULL",)),
        # #312 — entity_type + count checks missing in prod. Pin the full
        # entity_type value set, not just 'jurisdiction' (a re-add that dropped
        # 'role_assignment' would otherwise pass).
        (
            "field_confidence",
            "field_confidence_entity_type_check",
            ("organization", "person", "role_assignment", "jurisdiction"),
        ),
        (
            "import_provenance",
            "import_provenance_entity_type_check",
            ("organization", "person", "role_assignment", "jurisdiction"),
        ),
        ("import_batches", "import_batches_row_count_check", ("row_count >= 0",)),
        ("import_batches", "import_batches_loaded_count_check", ("loaded_count >= 0",)),
        ("import_batches", "import_batches_error_count_check", ("error_count >= 0",)),
    ],
)
async def test_apply_schema_adds_missing_check(db_pool, table, conname, expected_fragments):
    async with db_pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {conname}")
        assert await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname) is None

        await apply_schema(conn)
        constraint_def = await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname)
        assert constraint_def is not None
        for fragment in expected_fragments:
            assert fragment in constraint_def

        # Idempotent: a second apply must not re-add (guards are ADD-when-absent,
        # not unconditional DROP+ADD) — exactly one constraint of this name, same
        # definition. Regression on the #168/#312 mutual-exclusivity contract.
        await apply_schema(conn)
        assert await conn.fetchval(_CONSTRAINT_COUNT_SQL, table, conname) == 1
        assert await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname) == constraint_def


_CONFDELTYPE_SQL = """
SELECT confdeltype::text
FROM pg_constraint
WHERE conrelid = $1::regclass AND conname = $2
"""


async def test_apply_schema_repairs_fk_on_delete_action(db_pool):
    """apply_schema fixes an FK whose ON DELETE action drifted (#315).

    The #307/#312-class no-op masks modifiers, not just presence: prod's
    ``entity_events_event_place_address_id_fkey`` was NO ACTION (``confdeltype
    = 'a'``) while the inline ``ON DELETE SET NULL`` never applied to the
    pre-existing table. Reproduce that exact drift shape — constraint present,
    wrong action — then assert apply_schema repairs it to SET NULL
    (``confdeltype = 'n'``). Absence-only reconciliation (the CHECK harness
    above) would no-op here, so this needs its own confdeltype-keyed DO block.
    """
    table = "entity_events"
    conname = "entity_events_event_place_address_id_fkey"
    async with db_pool.acquire() as conn:
        # Simulate prod: drop the SET NULL variant, re-add as plain NO ACTION.
        await conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {conname}")
        await conn.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {conname} "
            "FOREIGN KEY (event_place_address_id) REFERENCES addresses(id)"
        )
        assert await conn.fetchval(_CONFDELTYPE_SQL, table, conname) == "a"

        await apply_schema(conn)
        assert await conn.fetchval(_CONFDELTYPE_SQL, table, conname) == "n"
        repaired_def = await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname)
        assert "ON DELETE SET NULL" in repaired_def

        # Idempotent: a second apply must not churn the already-correct FK.
        await apply_schema(conn)
        assert await conn.fetchval(_CONSTRAINT_COUNT_SQL, table, conname) == 1
        assert await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname) == repaired_def


async def test_apply_schema_swaps_the_full_overlay_index_for_the_partial_one(db_pool):
    """#498: a database that predates unpin-as-archive carries the full unique index,
    and `CREATE UNIQUE INDEX IF NOT EXISTS` no-ops on it by name — without its own
    block, every existing database would refuse a re-pin after an unpin."""
    async with db_pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS uq_curation_overlay_entity_field")
        await conn.execute(
            "CREATE UNIQUE INDEX uq_curation_overlay_entity_field"
            " ON curation_overlay (entity_type, entity_id, field)"
        )

        await apply_schema(conn)

        indexdef = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_curation_overlay_entity_field'"
        )
    assert "WHERE (archived_at IS NULL)" in indexdef


async def test_apply_schema_adds_archived_by_to_an_overlay_that_predates_it(db_pool):
    """CR 6: `archived_by` arrived after `archived_at`; a table without it gains it —
    with its foreign key — from `ADD COLUMN IF NOT EXISTS` alone."""
    async with db_pool.acquire() as conn:
        await conn.execute("ALTER TABLE curation_overlay DROP COLUMN IF EXISTS archived_by")

        await apply_schema(conn)

        fk = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'curation_overlay'::regclass AND contype = 'f'"
            "   AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (archived_by)%'"
        )
    assert fk == "FOREIGN KEY (archived_by) REFERENCES app_users(id) ON DELETE SET NULL"


async def test_apply_schema_adds_exported_producer_id_backfilled_from_the_export(db_pool):
    """#525: a crosswalk that predates `exported_producer_id` gains it, with every
    seeded row backfilled from `producer_id` (the anchor id it was keyed on) and
    the partial unique index the seed matches on. A row with no export — the
    applier's own create — stays NULL."""
    seeded, minted = generate_id(), generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE producer_crosswalk DROP COLUMN IF EXISTS exported_producer_id"
        )
        try:
            for row_id, sha in ((seeded, "sha256:test-525"), (minted, None)):
                await conn.execute(
                    "INSERT INTO producer_crosswalk"
                    " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution,"
                    "  export_sha256)"
                    " VALUES ($1, 'test-525', 'person', $1, $1, $1, 'live', $2)",
                    row_id,
                    sha,
                )

            await apply_schema(conn)

            exported = dict(
                await conn.fetch(
                    "SELECT id, exported_producer_id FROM producer_crosswalk WHERE id = ANY($1)",
                    [seeded, minted],
                )
            )
            indexdef = await conn.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_producer_crosswalk_exported'"
            )
        finally:
            await conn.execute(
                "DELETE FROM producer_crosswalk WHERE id = ANY($1)", [seeded, minted]
            )
    assert exported == {seeded: seeded, minted: None}
    assert "(source, kind, exported_producer_id)" in indexdef
    assert "WHERE (exported_producer_id IS NOT NULL)" in indexdef


async def test_apply_schema_adds_retracted_at_to_a_crosswalk_that_predates_it(db_pool):
    """#527: `ADD COLUMN IF NOT EXISTS` is the whole reconciliation — a nullable
    timestamp, NULL on every existing row, since nothing has been retracted yet."""
    async with db_pool.acquire() as conn:
        await conn.execute("ALTER TABLE producer_crosswalk DROP COLUMN IF EXISTS retracted_at")

        await apply_schema(conn)

        col = await conn.fetchrow(
            "SELECT data_type, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'producer_crosswalk' AND column_name = 'retracted_at'"
        )
    assert col is not None
    assert (col["data_type"], col["is_nullable"]) == ("timestamp with time zone", "YES")
