"""Unit tests for src.core.organizations.set_org_active (#241).

Run with:
    TEST_DATABASE_URL=postgres://... uv run pytest -m integration -v \
        tests/core/test_organizations.py
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.organizations import (
    ActiveOnArchivedOrg,
    OrgNotFound,
    set_org_active,
)

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    """Insert a minimal org (defaults active=TRUE); return its id."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def test_effective_change_writes_and_emits_one_change(db, org_id):
    """A real flip writes the flag and appends exactly one 'updated' row."""
    before = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
    await set_org_active(db, org_id, False)
    row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
    assert row["active"] is False
    rows = await db.fetch(
        "SELECT change_kind FROM entity_changes"
        " WHERE entity_id=$1 AND entity_type='organization' AND id > $2",
        org_id,
        before,
    )
    assert len(rows) == 1
    assert rows[0]["change_kind"] == "updated"


async def test_noop_emits_no_change_and_leaves_updated_at(db, org_id):
    """Re-asserting the current value is a true no-op: no event, no updated_at bump."""
    before_id = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
    before_ts = await db.fetchval("SELECT updated_at FROM organizations WHERE id=$1", org_id)
    await set_org_active(db, org_id, True)  # org defaults active=TRUE → unchanged
    rows = await db.fetch(
        "SELECT 1 FROM entity_changes WHERE entity_id=$1 AND id > $2", org_id, before_id
    )
    assert len(rows) == 0
    after_ts = await db.fetchval("SELECT updated_at FROM organizations WHERE id=$1", org_id)
    assert after_ts == before_ts


async def test_archived_org_rejected_and_untouched(db, org_id):
    """An archived org raises ActiveOnArchivedOrg without writing the flag."""
    await db.execute("UPDATE organizations SET archived_at=NOW() WHERE id=$1", org_id)
    with pytest.raises(ActiveOnArchivedOrg):
        await set_org_active(db, org_id, False)
    row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
    assert row["active"] is True  # untouched


async def test_missing_org_raises_not_found(db):
    """A vanished org (no row under the FOR UPDATE read) raises OrgNotFound."""
    with pytest.raises(OrgNotFound):
        await set_org_active(db, generate_id(), False)
