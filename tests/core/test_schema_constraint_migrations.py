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

from src.core.db import apply_schema

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
