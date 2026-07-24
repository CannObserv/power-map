"""Integration tests for scripts/split_speaker_designate.py (#314).

Splits Laurie Jinkins's single ``Speaker of the House`` tenure into the
``Speaker Designate`` (acting) role + the formal ``Speaker of the House`` role,
mirroring the ``Acting Chair`` / ``Chair`` pattern on WA House COG (#266). Sets
the recovered dates and folds the housedemocrats.wa.gov citations into notes.

Requires TEST_DATABASE_URL + a schema-applied DB. Run via:
    uv run pytest tests/scripts/test_split_speaker_designate.py
"""

import datetime

import pytest
import pytest_asyncio

from scripts.split_speaker_designate import (
    DESIGNATE_END,
    DESIGNATE_START,
    DESIGNATE_TITLE,
    DESIGNATE_URL,
    JINKINS_PERSON_ID,
    SPEAKER_ASSIGNMENT_ID,
    SPEAKER_ROLE_ID,
    SPEAKER_START,
    SPEAKER_URL,
    WA_HOUSE_ORG_ID,
    split_speaker_designate,
)
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


async def _seed(db) -> None:
    """Recreate the pre-#314 prod state: one NULL/NULL is_current Speaker tenure."""
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", WA_HOUSE_ORG_ID)
    await db.execute("INSERT INTO people (id) VALUES ($1)", JINKINS_PERSON_ID)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id, notes)"
        " VALUES ($1,$2,'Speaker of the House',"
        " (SELECT id FROM role_types WHERE slug='chamber_leader'),$3)",
        SPEAKER_ROLE_ID,
        WA_HOUSE_ORG_ID,
        "Tenure 2021-23 (from legacy title) — assignment dates need review",
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)"
        " VALUES ($1,$2,$3,TRUE,NULL,NULL)",
        SPEAKER_ASSIGNMENT_ID,
        JINKINS_PERSON_ID,
        SPEAKER_ROLE_ID,
    )


async def _designate_role(db):
    return await db.fetchrow(
        "SELECT r.id, r.title, r.notes, rt.slug AS role_type FROM roles r"
        " LEFT JOIN role_types rt ON rt.id = r.role_type_id"
        " WHERE r.organization_id=$1 AND r.title=$2 AND r.archived_at IS NULL",
        WA_HOUSE_ORG_ID,
        DESIGNATE_TITLE,
    )


async def _designate_assignment(db, role_id: str):
    return await db.fetchrow(
        "SELECT is_current, start_date, end_date, notes FROM role_assignments"
        " WHERE person_id=$1 AND role_id=$2 AND archived_at IS NULL",
        JINKINS_PERSON_ID,
        role_id,
    )


async def test_creates_designate_role_typed_chamber_leader(db):
    """A new Speaker Designate role appears, coarse-typed like the formal one."""
    await _seed(db)
    await split_speaker_designate(db, execute=True)

    role = await _designate_role(db)
    assert role is not None
    assert role["role_type"] == "chamber_leader"
    assert role["notes"]  # office description present


async def test_designate_assignment_dates_and_citation(db):
    """Designate tenure = election → day before swearing-in, closed, cited."""
    await _seed(db)
    await split_speaker_designate(db, execute=True)

    role = await _designate_role(db)
    a = await _designate_assignment(db, role["id"])
    assert a is not None
    assert a["start_date"] == DESIGNATE_START
    assert a["end_date"] == DESIGNATE_END
    assert a["is_current"] is False
    assert DESIGNATE_URL in a["notes"]


async def test_formal_speaker_dates_currency_and_citation(db):
    """Formal Speaker keeps is_current, gains the swearing-in start + citation."""
    await _seed(db)
    await split_speaker_designate(db, execute=True)

    a = await db.fetchrow(
        "SELECT is_current, start_date, end_date, notes FROM role_assignments WHERE id=$1",
        SPEAKER_ASSIGNMENT_ID,
    )
    assert a["start_date"] == SPEAKER_START
    assert a["end_date"] is None
    assert a["is_current"] is True
    assert SPEAKER_URL in a["notes"]


async def test_stale_tenure_breadcrumb_cleared(db):
    """The legacy '2021-23' note on the formal-Speaker role is removed."""
    await _seed(db)
    await split_speaker_designate(db, execute=True)

    notes = await db.fetchval("SELECT notes FROM roles WHERE id=$1", SPEAKER_ROLE_ID)
    assert notes is None or "2021-23" not in notes


async def test_dry_run_writes_nothing(db):
    """execute=False plans but mutates no rows."""
    await _seed(db)
    await split_speaker_designate(db, execute=False)

    assert await _designate_role(db) is None
    a = await db.fetchrow(
        "SELECT start_date, notes FROM role_assignments WHERE id=$1", SPEAKER_ASSIGNMENT_ID
    )
    assert a["start_date"] is None
    assert "2021-23" in await db.fetchval("SELECT notes FROM roles WHERE id=$1", SPEAKER_ROLE_ID)


async def test_idempotent_second_run(db):
    """Re-running changes nothing further: one designate role, same dates."""
    await _seed(db)
    await split_speaker_designate(db, execute=True)
    await split_speaker_designate(db, execute=True)

    rows = await db.fetch(
        "SELECT id FROM roles WHERE organization_id=$1 AND title=$2 AND archived_at IS NULL",
        WA_HOUSE_ORG_ID,
        DESIGNATE_TITLE,
    )
    assert len(rows) == 1
    role = await _designate_role(db)
    a = await _designate_assignment(db, role["id"])
    assert a["start_date"] == DESIGNATE_START and a["end_date"] == DESIGNATE_END


async def test_identity_guard_rejects_drifted_assignment(db):
    """Guard raises (no mutation) if the formal assignment no longer holds Jinkins."""
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", WA_HOUSE_ORG_ID)
    other = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id)"
        " VALUES ($1,$2,'Speaker of the House',"
        " (SELECT id FROM role_types WHERE slug='chamber_leader'))",
        SPEAKER_ROLE_ID,
        WA_HOUSE_ORG_ID,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current) VALUES ($1,$2,$3,TRUE)",
        SPEAKER_ASSIGNMENT_ID,
        other,
        SPEAKER_ROLE_ID,
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        await split_speaker_designate(db, execute=True)

    assert await _designate_role(db) is None  # nothing created before the guard fired


def test_designate_window_precedes_speaker_start():
    """Sanity on the constants: no overlap between the two tenures."""
    assert DESIGNATE_START < DESIGNATE_END < SPEAKER_START
    assert DESIGNATE_END == SPEAKER_START - datetime.timedelta(days=1)
