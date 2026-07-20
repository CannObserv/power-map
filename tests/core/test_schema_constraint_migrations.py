"""apply_schema restores inline CHECKs on pre-existing DBs (#307 CR, #312).

``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so constraints
added inline to the CREATE never reach a DB whose table predates them — prod
was missing ``entity_events_event_year_check`` (and ``chk_at_requires_year``)
entirely while fresh DBs had both. The #312 sweep found five more in the same
state (``field_confidence``/``import_provenance`` entity_type checks +
``import_batches`` count checks): their only reconciliation was a
replace-if-stale ``IF EXISTS (... NOT LIKE ...)`` guard (or none at all), which
no-ops when the constraint is *entirely absent*. The companion DO blocks must
ADD each constraint when absent, not only replace an old clause.

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


@pytest.mark.parametrize(
    ("table", "conname", "expected_fragment"),
    [
        # #307 CR — entity_events.
        ("entity_events", "entity_events_event_year_check", "event_year <> 0"),
        ("entity_events", "chk_at_requires_year", "event_at IS NULL"),
        # #312 — entity_type + count checks missing in prod.
        ("field_confidence", "field_confidence_entity_type_check", "jurisdiction"),
        ("import_provenance", "import_provenance_entity_type_check", "jurisdiction"),
        ("import_batches", "import_batches_row_count_check", "row_count >= 0"),
        ("import_batches", "import_batches_loaded_count_check", "loaded_count >= 0"),
        ("import_batches", "import_batches_error_count_check", "error_count >= 0"),
    ],
)
async def test_apply_schema_adds_missing_check(db_pool, table, conname, expected_fragment):
    async with db_pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {conname}")
        assert await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname) is None
        await apply_schema(conn)
        constraint_def = await conn.fetchval(_CONSTRAINT_DEF_SQL, table, conname)
        assert constraint_def is not None
        assert expected_fragment in constraint_def
