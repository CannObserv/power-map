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
from tests.db_utils import row_version

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


async def test_effective_change_writes_and_touches_the_row(db, org_id):
    """A real flip writes the flag and UPDATEs the org row."""
    before = await row_version(db, "organizations", org_id)
    await set_org_active(db, org_id, False)
    row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
    assert row["active"] is False
    assert await row_version(db, "organizations", org_id) != before


async def test_noop_leaves_the_row_and_updated_at(db, org_id):
    """Re-asserting the current value is a true no-op: no UPDATE, no updated_at bump."""
    before = await row_version(db, "organizations", org_id)
    before_ts = await db.fetchval("SELECT updated_at FROM organizations WHERE id=$1", org_id)
    await set_org_active(db, org_id, True)  # org defaults active=TRUE → unchanged
    assert await row_version(db, "organizations", org_id) == before
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
