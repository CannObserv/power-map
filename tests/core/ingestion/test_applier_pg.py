"""The asyncpg-backed `LiveStore` against a real database (#527).

The engine's matching is proven on the fake; what only Postgres can answer —
what the columns are called, how NULLs compare in a tuple — is proven here.
"""

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402

pytestmark = [pytest.mark.integration]

SOURCE = "test-527"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def test_the_crosswalk_read_carries_retracted_at(db):
    """Scope needs it to tell the applier's own archives from a curator's."""
    stamped, clean = generate_id(), generate_id()
    for producer_id, stamp in ((stamped, "now()"), (clean, "NULL")):
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution, retracted_at)"
            f" VALUES ($1, $2, 'assignment', $3, $3, $3, 'live', {stamp})",
            generate_id(),
            SOURCE,
            producer_id,
        )

    rows = await PostgresLiveStore(db).crosswalk(SOURCE, ["assignment"])

    retracted = {r["producer_id"]: r["retracted_at"] is not None for r in rows}
    assert retracted == {stamped: True, clean: False}


async def test_value_rows_serves_every_name_whatever_its_visibility(db):
    """#533: the create hint matches in Python, so the store hands over the raw rows.

    A twin may hold only a non-public name; the hint names the parent and never
    this text, so the read carries no visibility predicate by design.
    """
    person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    for name, visibility in (("Mike Kreidler", "public"), ("Myron Kreidler", "hidden")):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, visibility)"
            " VALUES ($1, $2, $3, 'legal', $4)",
            generate_id(),
            person,
            name,
            visibility,
        )

    rows = await PostgresLiveStore(db).value_rows("person_names", "name", "person_id")

    assert sorted(r for r in rows if r[1] == person) == [
        ("Mike Kreidler", person),
        ("Myron Kreidler", person),
    ]
