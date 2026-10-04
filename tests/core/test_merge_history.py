"""Walking an id through ``deleted_entities`` merge history (#495, #607).

The walk's edge cases are pinned by ``tests/core/ingestion/test_crosswalk_resolve.py``
through the crosswalk's anchor kinds. This module pins what moved into core with
it: the walk is keyed by tombstone ``entity_type``, so the public read path can
use it for every tombstoned type without the crosswalk's vocabulary.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.merge_history import Resolution, fetch_tombstone, walk_merge_history

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


async def _tombstone(db, entity_type: str, entity_id: str, merged_into: str | None) -> None:
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into) VALUES ($1,$2,$3)",
        entity_type,
        entity_id,
        merged_into,
    )


async def test_walk_is_keyed_by_tombstone_type(db):
    """``role_assignment`` is a tombstone type the crosswalk calls ``assignment``."""
    person, org, role, ra = generate_id(), generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute("INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'T')", role, org)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)", ra, person, role
    )
    loser = generate_id()
    await _tombstone(db, "role_assignment", loser, ra)

    assert await walk_merge_history(db, "role_assignment", loser) == Resolution("merged", ra)


async def test_walk_rejects_an_unknown_type(db):
    with pytest.raises(ValueError, match="unknown entity type"):
        await walk_merge_history(db, "role_assignment_relationship", generate_id())


async def test_fetch_tombstone_returns_the_row_or_none(db):
    gone = generate_id()
    await _tombstone(db, "jurisdiction", gone, None)

    row = await fetch_tombstone(db, "jurisdiction", gone)

    assert row is not None and row["merged_into"] is None and row["deleted_at"] is not None
    assert await fetch_tombstone(db, "jurisdiction", generate_id()) is None
