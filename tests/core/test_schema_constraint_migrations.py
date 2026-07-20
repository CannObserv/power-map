"""apply_schema restores inline entity_events CHECKs on pre-existing DBs (#307 CR).

``CREATE TABLE IF NOT EXISTS`` no-ops on an existing table, so constraints
added inline to the CREATE never reach a DB whose table predates them — prod
was missing ``entity_events_event_year_check`` (and ``chk_at_requires_year``)
entirely while fresh DBs had both. The companion DO blocks must ADD each
constraint when absent, not only replace an old clause.

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
WHERE conrelid = 'entity_events'::regclass AND conname = $1
"""


@pytest.mark.parametrize(
    ("conname", "expected_fragment"),
    [
        ("entity_events_event_year_check", "event_year <> 0"),
        ("chk_at_requires_year", "event_at IS NULL"),
    ],
)
async def test_apply_schema_adds_missing_entity_events_check(db_pool, conname, expected_fragment):
    async with db_pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE entity_events DROP CONSTRAINT IF EXISTS {conname}")
        assert await conn.fetchval(_CONSTRAINT_DEF_SQL, conname) is None
        await apply_schema(conn)
        constraint_def = await conn.fetchval(_CONSTRAINT_DEF_SQL, conname)
        assert constraint_def is not None
        assert expected_fragment in constraint_def
