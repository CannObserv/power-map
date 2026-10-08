"""#609: the daily audit counts every polymorphic row left naming a missing entity.

The admin hard delete drops an entity's polymorphic rows (#605), but a script,
raw SQL or a merge regression can still delete the entity alone. These tests
delete it that way, past ``delete_entity_ancillary``, and assert the counter
sees each row it strands — every table the #605 ratchet seeds, for every type
the table admits — plus another entity's event still linking to it (#611).
"""

import asyncpg
import pytest
import pytest_asyncio

from src.core.ancillary_migrate import (
    ENTITY_TABLES,
    HARD_DELETABLE_TYPES,
    count_orphaned_polymorphic_rows,
)
from src.core.db import generate_id
from tests.polymorphic_seeders import SEEDERS, archived_entity, seed_entity_events

pytestmark = [pytest.mark.integration]

#: Seeded rows the counter does not own: citations are counted per type by
#: ``count_orphaned_citations``; ``addresses`` is not polymorphic.
_COUNTED_ELSEWHERE = {"citations", "addresses"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_all(db, entity_type: str, entity_id: str) -> set[str]:
    """One row in every polymorphic table admitting ``entity_type``; the tables seeded."""
    seeded: list[tuple[str, str]] = []
    for table, seed in SEEDERS.items():
        if table in _COUNTED_ELSEWHERE:
            continue
        try:
            async with db.transaction():
                seeded += await seed(db, entity_type, entity_id)
        except asyncpg.CheckViolationError:
            pass  # the table's CHECK does not admit this entity type
    return {table for table, _ in seeded} - _COUNTED_ELSEWHERE


async def _raw_delete(db, entity_type: str, entity_id: str) -> None:
    """Delete the entity alone, as a script would — no ``delete_entity_ancillary``."""
    table = ENTITY_TABLES[entity_type]
    await db.execute(f"DELETE FROM {table} WHERE id = $1", entity_id)


def _grown(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    assert after.keys() == before.keys()
    return {key: n - before[key] for key, n in after.items() if n != before[key]}


@pytest.mark.parametrize("entity_type", sorted(HARD_DELETABLE_TYPES))
async def test_counts_every_row_a_raw_delete_strands(db, entity_type):
    eid = await archived_entity(db, entity_type)
    tables = await _seed_all(db, entity_type, eid)
    if entity_type != "role":  # the #617 shape: an identifier off its type's table
        assert "identifiers" in tables

    before = await count_orphaned_polymorphic_rows(db)
    await _raw_delete(db, entity_type, eid)
    after = await count_orphaned_polymorphic_rows(db)

    assert _grown(before, after) == {f"{entity_type}.{t}": 1 for t in tables}


@pytest.mark.parametrize("entity_type", sorted(HARD_DELETABLE_TYPES))
async def test_ignores_a_live_entitys_rows(db, entity_type):
    before = await count_orphaned_polymorphic_rows(db)
    await _seed_all(db, entity_type, await archived_entity(db, entity_type))

    assert _grown(before, await count_orphaned_polymorphic_rows(db)) == {}


@pytest.mark.parametrize("entity_type", ["organization", "person"])
async def test_counts_another_entitys_event_linking_a_missing_entity(db, entity_type):
    """#611's inbound kind: the owner lives, the entity its event links to does not."""
    gone = await archived_entity(db, entity_type)
    owner = await archived_entity(db, "organization")
    [(_, event_id), _] = await seed_entity_events(db, "organization", owner)
    await db.execute(
        "UPDATE entity_events SET linked_entity_type = $1, linked_entity_id = $2 WHERE id = $3",
        entity_type,
        gone,
        event_id,
    )

    before = await count_orphaned_polymorphic_rows(db)
    await _raw_delete(db, entity_type, gone)
    after = await count_orphaned_polymorphic_rows(db)

    assert _grown(before, after) == {f"{entity_type}.entity_events_linked": 1}


async def test_ignores_an_import_error_rows_placeholder_id(db):
    """The importer stamps a rejected row with a fresh id no entity ever had."""
    before = await count_orphaned_polymorphic_rows(db)
    batch_id = generate_id()
    await db.execute(
        "INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count,"
        " error_count) VALUES ($1, 'bad.csv', 'h', 1, 0, 1)",
        batch_id,
    )
    await db.execute(
        "INSERT INTO import_provenance (id, batch_id, source_row, entity_type, entity_id,"
        " action, raw_data) VALUES ($1, $2, 2, 'organization', $3, 'error', '{}')",
        generate_id(),
        batch_id,
        generate_id(),
    )

    assert _grown(before, await count_orphaned_polymorphic_rows(db)) == {}
