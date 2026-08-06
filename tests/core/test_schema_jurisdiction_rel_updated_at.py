"""Integration tests: `jurisdiction_relationships.updated_at` (#392 PR-C).

The table shipped with only `created_at` while being **mutable** — the admin
panel (`jurisdictions_relationships.py`) UPDATEs edges in place. Without a
watermark there is nothing for a conditional GET to key on that survives an
edit, so the endpoint could not join the #392 surface.

Two halves, both required:

- the column itself, reaching a DB whose table predates it (`CREATE TABLE IF
  NOT EXISTS` no-ops on an existing table — the #307/#312/#315 drift class), and
- `trg_updated_at_jurisdiction_relationships`, without which the column would
  freeze at insert time and the watermark would be a lie.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


OLD_TS = "'2001-02-03'::timestamptz"


async def _edge(db, *, backdated: bool = False) -> str:
    """Create two jurisdictions and a lineage edge between them.

    *backdated* stamps ``updated_at`` at INSERT. It cannot be set by a later
    UPDATE: the BEFORE-UPDATE trigger overwrites any supplied value with NOW()
    — which is exactly the "never set updated_at manually" convention holding.
    """
    a, b, rid = generate_id(), generate_id(), generate_id()
    jtype = await db.fetchval("SELECT id FROM jurisdiction_types LIMIT 1")
    for jid, slug in ((a, f"cg-a-{a}"), (b, f"cg-b-{b}")):
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            slug,
            f"Probe {slug}",
            jtype,
        )
    rel_type = await db.fetchval("SELECT id FROM jurisdiction_relationship_types LIMIT 1")
    if backdated:
        await db.execute(
            "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id, updated_at)"
            f" VALUES ($1,$2,$3,$4,{OLD_TS})",
            rid,
            a,
            b,
            rel_type,
        )
    else:
        await db.execute(
            "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
            " VALUES ($1,$2,$3,$4)",
            rid,
            a,
            b,
            rel_type,
        )
    return rid


async def test_updated_at_column_exists_and_is_not_null(db):
    row = await db.fetchrow(
        """
        SELECT data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'jurisdiction_relationships' AND column_name = 'updated_at'
        """
    )
    assert row is not None, "jurisdiction_relationships.updated_at missing"
    assert row["data_type"] == "timestamp with time zone"
    assert row["is_nullable"] == "NO"
    assert row["column_default"] is not None, "no DEFAULT — a plain INSERT would violate NOT NULL"


async def test_insert_populates_updated_at(db):
    rid = await _edge(db)
    row = await db.fetchrow(
        "SELECT created_at, updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )
    assert row["updated_at"] is not None
    assert row["updated_at"] == row["created_at"]


async def test_update_advances_updated_at(db):
    """The trigger is the point — a frozen column makes the watermark a lie."""
    rid = await _edge(db, backdated=True)
    before = await db.fetchval(
        "SELECT updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )

    await db.execute("UPDATE jurisdiction_relationships SET notes = 'edited' WHERE id = $1", rid)

    after = await db.fetchval(
        "SELECT updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )
    assert after > before, "trg_updated_at_jurisdiction_relationships did not fire"


async def test_supersede_advances_updated_at(db):
    """Soft-retire (`superseded_at`) is an edit like any other."""
    rid = await _edge(db, backdated=True)
    before = await db.fetchval(
        "SELECT updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )
    await db.execute(
        "UPDATE jurisdiction_relationships SET superseded_at = NOW() WHERE id = $1", rid
    )
    after = await db.fetchval(
        "SELECT updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )
    assert after > before


async def test_trigger_is_registered(db):
    """Named guard: the parity audit (#331) diffs triggers by name prod-vs-reference."""
    found = await db.fetchval(
        """
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'jurisdiction_relationships'
          AND t.tgname = 'trg_updated_at_jurisdiction_relationships'
          AND NOT t.tgisinternal
        """
    )
    assert found == 1


# ---------------------------------------------------------------------------
# The reconciliation path (CR #392/11, #392/12)
# ---------------------------------------------------------------------------
#
# Every test above runs against a DB whose table was created *fresh* with the
# inline column, so `ADD COLUMN IF NOT EXISTS` no-ops and the backfill matches
# zero rows — the half of the change that only executes on a DB predating the
# column is structurally invisible to them. That is exactly how CR finding 11
# (the backfill being clobbered by its own trigger) passed a green suite.
#
# These two rebuild the pre-migration shape and replay the real statements, in
# the order `apply_schema` would execute them.

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "core" / "schema.sql"

_ADD_COLUMN_MARKER = (
    "ALTER TABLE jurisdiction_relationships ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;"
)
_SET_NOT_NULL_MARKER = (
    "ALTER TABLE jurisdiction_relationships ALTER COLUMN updated_at SET NOT NULL;"
)
_TRIGGER_MARKER = "CREATE OR REPLACE TRIGGER trg_updated_at_jurisdiction_relationships"


def _reconciliation_block(sql: str) -> str:
    start = sql.index(_ADD_COLUMN_MARKER)
    end = sql.index(_SET_NOT_NULL_MARKER) + len(_SET_NOT_NULL_MARKER)
    return sql[start:end]


def test_reconciliation_runs_before_the_trigger_is_created():
    """Static guard: file order *is* execution order (`apply_schema` phase 1).

    The backfill computes a historical timestamp; the BEFORE-UPDATE trigger
    overwrites `NEW.updated_at` with NOW() unconditionally. If the trigger is
    created first, the backfill is silently defeated.
    """
    sql = SCHEMA_SQL.read_text()
    assert sql.index(_SET_NOT_NULL_MARKER) < sql.index(_TRIGGER_MARKER), (
        "updated_at reconciliation must precede trg_updated_at_jurisdiction_relationships "
        "— otherwise the trigger clobbers the backfill with NOW()"
    )


async def test_backfill_preserves_historical_timestamps(db):
    """Behavioural: rebuild the pre-migration shape and replay schema.sql for real.

    Drops the trigger and the column (transactional DDL, rolled back with the
    fixture), inserts a backdated edge, then executes the reconciliation block
    and the trigger statement **in the order they appear in schema.sql**.

    Note for a future parallel runner: the DDL takes an ACCESS EXCLUSIVE lock on
    `jurisdiction_relationships` for this transaction. Harmless while the suite
    runs serially; under pytest-xdist this module must be isolated from anything
    else touching that table.
    """
    sql = SCHEMA_SQL.read_text()
    await db.execute(
        "DROP TRIGGER trg_updated_at_jurisdiction_relationships ON jurisdiction_relationships"
    )
    await db.execute("ALTER TABLE jurisdiction_relationships DROP COLUMN updated_at")

    rid = await _edge(db)
    await db.execute(
        f"UPDATE jurisdiction_relationships SET created_at = {OLD_TS} WHERE id = $1", rid
    )

    statements = sorted(
        [
            (sql.index(_ADD_COLUMN_MARKER), _reconciliation_block(sql)),
            (sql.index(_TRIGGER_MARKER), sql[sql.index(_TRIGGER_MARKER) :].split(";")[0] + ";"),
        ]
    )
    for _, stmt in statements:
        await db.execute(stmt)

    row = await db.fetchrow(
        "SELECT created_at, updated_at FROM jurisdiction_relationships WHERE id = $1", rid
    )
    assert row["updated_at"] == row["created_at"], (
        "backfill was clobbered — every historical edge now claims it changed at deploy time"
    )
    assert row["updated_at"].year == 2001
