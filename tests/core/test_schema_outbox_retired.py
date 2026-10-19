"""The ``entity_changes`` outbox is retired, and the touch triggers are not (#651).

The outbox lost its last reader with ``GET /changes`` (#502), so its table, its
two writer functions and their seven row triggers are dropped. The #327 touch
triggers that used to cascade into it stay: they bump the parent's
``updated_at``, which the detail ETags key on (#392). ETag behaviour itself is
asserted end to end in ``tests/api/public/test_ancillary_touch_etag.py``.

The drop rides ``apply_schema`` (ExecStartPre on every restart), so it must
reconcile a database that still carries the outbox, and be a no-op on one that
does not.
"""

import re

import pytest
import pytest_asyncio

from src.core.db import SCHEMA_PATH, apply_schema, generate_id

pytestmark = [pytest.mark.integration]

_OUTBOX_FUNCTIONS = ("fn_record_entity_change", "fn_record_deleted_entity_change")
_OUTBOX_TRIGGERS = {
    "people": "trg_entity_changes_people",
    "organizations": "trg_entity_changes_organizations",
    "jurisdictions": "trg_entity_changes_jurisdictions",
    "roles": "trg_entity_changes_roles",
    "role_assignments": "trg_entity_changes_role_assignments",
    "role_assignment_relationships": "trg_entity_changes_role_assignment_relationships",
    "deleted_entities": "trg_entity_changes_deleted",
}

_TOUCH_TRIGGER_RE = re.compile(r"CREATE OR REPLACE TRIGGER (trg_touch_\w+)\s+\w+ [^;]*? ON (\w+)")


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _outbox_objects(db) -> dict[str, list[str]]:
    """Every outbox object still present, by kind (empty lists when retired)."""
    table = await db.fetchval("SELECT to_regclass('public.entity_changes')::text")
    functions = await db.fetch(
        "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND proname = ANY($1::text[])",
        list(_OUTBOX_FUNCTIONS),
    )
    triggers = await db.fetch(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_entity_changes%'"
    )
    return {
        "table": [table] if table else [],
        "functions": sorted(r["proname"] for r in functions),
        "triggers": sorted(r["tgname"] for r in triggers),
    }


async def _install_legacy_outbox(db) -> None:
    """Recreate the pre-#651 outbox shape: table, both writers, all seven triggers."""
    await db.execute(
        "CREATE TABLE entity_changes (id BIGSERIAL PRIMARY KEY, entity_type TEXT NOT NULL,"
        " entity_id TEXT NOT NULL, change_kind TEXT NOT NULL,"
        " changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), merged_into TEXT, source_key_id TEXT)"
    )
    await db.execute("CREATE INDEX idx_entity_changes_entity ON entity_changes (entity_id, id)")
    for fn in _OUTBOX_FUNCTIONS:
        await db.execute(
            f"CREATE FUNCTION {fn}() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN"
            " INSERT INTO entity_changes (entity_type, entity_id, change_kind)"
            " VALUES ('person', 'x', 'updated'); RETURN NEW; END; $$"
        )
    for table, trigger in _OUTBOX_TRIGGERS.items():
        fn = _OUTBOX_FUNCTIONS[1] if table == "deleted_entities" else _OUTBOX_FUNCTIONS[0]
        await db.execute(
            f"CREATE TRIGGER {trigger} AFTER INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )


async def test_outbox_absent_after_apply(db):
    assert await _outbox_objects(db) == {"table": [], "functions": [], "triggers": []}


async def test_apply_schema_drops_a_live_outbox(db):
    """A DB that still carries the outbox (prod before deploy) loses all of it."""
    await _install_legacy_outbox(db)
    present = await _outbox_objects(db)
    assert present["table"] and len(present["triggers"]) == len(_OUTBOX_TRIGGERS)

    await apply_schema(db)

    assert await _outbox_objects(db) == {"table": [], "functions": [], "triggers": []}
    # An entity write no longer reaches a dropped table through a stale trigger.
    await db.execute("INSERT INTO people (id) VALUES ($1)", generate_id())


async def test_apply_schema_is_idempotent_once_retired(db):
    await apply_schema(db)
    await apply_schema(db)
    assert await _outbox_objects(db) == {"table": [], "functions": [], "triggers": []}


async def test_every_declared_touch_trigger_is_registered(db):
    """The touch triggers survive the drop: each one schema.sql declares is live."""
    declared = set(_TOUCH_TRIGGER_RE.findall(SCHEMA_PATH.read_text()))
    assert len(declared) >= 13, f"touch-trigger parse found too few: {sorted(declared)}"
    rows = await db.fetch(
        "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
        " WHERE NOT t.tgisinternal AND t.tgenabled != 'D' AND t.tgname LIKE 'trg_touch_%'"
    )
    registered = {(r["tgname"], r["relname"]) for r in rows}
    assert declared <= registered, f"declared but not live: {sorted(declared - registered)}"
