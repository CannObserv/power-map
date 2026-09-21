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
