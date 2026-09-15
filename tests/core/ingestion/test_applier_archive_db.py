"""Archive, restore and identity creates against a real database (#527 step 7).

The diff is proven on the fake; what only Postgres can answer is proven here:
the partial identity index's NULLs, the #301 cascade that archives an
assignment's relationships with it, the CHECK a reopened span must satisfy, and
that a plan the engine calls clean commits and re-diffs clean in one transaction.
"""

from datetime import date

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402
from src.core.ingestion.applier_write import apply_diff  # noqa: E402
from tests.core.ingestion.applier_fakes import manifest_with_assignments  # noqa: E402

pytestmark = [pytest.mark.integration]

SOURCE = "test-527"
MANIFEST = manifest_with_assignments()
SPANS, DATES = "desired_role_assignments", "desired_role_assignment_dates"
Q, R = "01Q527", "01R527"  # the person's and the role's producer ids
FIRST, SECOND = date(2021, 1, 11), date(2023, 1, 9)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _anchor(db, kind, producer_id, pm_id, *, retracted=False):
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution, retracted_at)"
        f" VALUES ($1, $2, $3, $4, $5, $5, 'live', {'now()' if retracted else 'NULL'})",
        generate_id(),
        SOURCE,
        kind,
        producer_id,
        pm_id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def world(db):
    """A person and a role, both anchored; the assignments are each test's own."""
    person, org, role = generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", role, org
    )
    await _anchor(db, "person", Q, person)
    await _anchor(db, "role", R, role)
    return {"person": person, "role": role}


async def _ra(db, world, start, *, end=None, current=False, archived=False) -> str:
    ra = generate_id()
    await db.execute(
        "INSERT INTO role_assignments"
        " (id, person_id, role_id, start_date, end_date, is_current, archived_at)"
        f" VALUES ($1, $2, $3, $4, $5, $6, {'now()' if archived else 'NULL'})",
        ra,
        world["person"],
        world["role"],
        start,
        end,
        current,
    )
    return ra


def _span(producer_id, pm_id, start, *, person=Q):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "person_producer_id": person,
        "role_producer_id": R,
        "start_date": start,
    }


def _dates(producer_id, pm_id, start, end=None):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "start_date": start,
        "end_date": end,
        "is_current": end is None,
    }


def _state(*, spans=(), dates=(), people=()) -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables[SPANS], tables[DATES], tables["desired_people"] = list(spans), list(dates), list(people)
    return DesiredState(tables=tables, build_info=None)


async def _diff(db, state):
    return await diff_desired(state, MANIFEST, PostgresLiveStore(db), source=SOURCE)


async def _apply(db, state):
    store = PostgresLiveStore(db)
    diff = await diff_desired(state, MANIFEST, store, source=SOURCE)

    async def rediff(minted):
        return await diff_desired(state, MANIFEST, store, source=SOURCE, minted=minted)

    result = await apply_diff(diff, MANIFEST, db, source=SOURCE, rediff=rediff)
    return diff, result


def _entry(diff, producer_id, table=SPANS):
    [entry] = [e for e in diff.entries if e.table == table and e.producer_id == producer_id]
    return entry


async def _retracted(db, producer_id) -> bool:
    return await db.fetchval(
        "SELECT retracted_at IS NOT NULL FROM producer_crosswalk WHERE source = $1"
        " AND kind = 'assignment' AND producer_id = $2",
        SOURCE,
        producer_id,
    )


async def test_an_archive_stamps_its_anchor_and_archives_the_relationships_on_it(db, world):
    kept = await _ra(db, world, FIRST, current=True)
    gone = await _ra(db, world, SECOND, current=True)
    await _anchor(db, "assignment", "S1", kept)
    await _anchor(db, "assignment", "S2", gone)
    rel_type = await db.fetchval(
        "SELECT id FROM role_assignment_relationship_types ORDER BY id LIMIT 1"
    )
    edge = generate_id()
    await db.execute(
        "INSERT INTO role_assignment_relationships"
        " (id, from_assignment_id, to_assignment_id, rel_type_id) VALUES ($1, $2, $3, $4)",
        edge,
        gone,
        kept,
        rel_type,
    )

    diff, _ = await _apply(db, _state(spans=[_span("S1", kept, FIRST)]))

    archive = _entry(diff, "S2")
    assert archive.kind == "archive"
    assert archive.effects == {
        "superseded_by": ["S1"],
        "cascades": {"role_assignment_relationships": 1},
    }
    assert await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM role_assignments WHERE id = $1", gone
    )
    assert await _retracted(db, "S2") is True
    # The restore's provenance test (CR 1): the stamp is the archive's own time.
    assert await db.fetchval(
        "SELECT c.retracted_at = r.archived_at FROM producer_crosswalk c"
        " JOIN role_assignments r ON r.id = c.pm_id"
        " WHERE c.source = $1 AND c.kind = 'assignment' AND c.producer_id = 'S2'",
        SOURCE,
    )
    assert await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM role_assignment_relationships WHERE id = $1", edge
    )


async def test_a_restore_unarchives_the_row_and_clears_its_stamp(db, world):
    ra = await _ra(db, world, FIRST, archived=True)
    await _anchor(db, "assignment", "S1", ra, retracted=True)

    diff, _ = await _apply(db, _state(spans=[_span("S1", ra, FIRST)]))

    assert _entry(diff, "S1").kind == "restore"
    assert await db.fetchval("SELECT archived_at FROM role_assignments WHERE id = $1", ra) is None
    assert await _retracted(db, "S1") is False


@pytest.mark.parametrize("start", [FIRST, None], ids=["dated", "null start"])
async def test_a_restore_onto_a_held_slot_is_a_conflict_before_any_write(db, world, start):
    """#424, and the index is NULLS NOT DISTINCT: two unknown starts are one slot."""
    ra = await _ra(db, world, start, archived=True)
    holder = await _ra(db, world, start)
    await _anchor(db, "assignment", "S1", ra, retracted=True)

    entry = _entry(await _diff(db, _state(spans=[_span("S1", ra, start)])), "S1")

    assert entry.kind == "conflict"
    assert holder in entry.reason


async def test_usa_wa_289_archives_the_later_segment_and_reopens_the_first(db, world):
    """A collapsed party span, in one verified transaction: the CHECK holds throughout."""
    first = await _ra(db, world, FIRST, end=date(2022, 12, 31), current=False)
    second = await _ra(db, world, SECOND, current=True)
    await _anchor(db, "assignment", "S1", first)
    await _anchor(db, "assignment", "S2", second)

    diff, result = await _apply(
        db, _state(spans=[_span("S1", first, FIRST)], dates=[_dates("S1", first, FIRST)])
    )

    assert _entry(diff, "S2").kind == "archive"
    assert _entry(diff, "S1", DATES).kind == "update"
    row = await db.fetchrow(
        "SELECT end_date, is_current, archived_at FROM role_assignments WHERE id = $1", first
    )
    assert (row["end_date"], row["is_current"], row["archived_at"]) == (None, True, None)
    assert {e.kind for e in result.after.entries if e.table in (SPANS, DATES)} == {"noop"}


async def test_a_person_and_its_assignment_are_created_in_one_run(db, world):
    """A new legislator: the span names a person this very plan mints."""
    diff, result = await _apply(
        db,
        _state(
            people=[{"pm_id": None, "producer_id": "01QNEW527"}],
            spans=[_span("S_NEW", None, SECOND, person="01QNEW527")],
            dates=[_dates("S_NEW", None, SECOND)],
        ),
    )

    assert _entry(diff, "S_NEW").kind == "create"
    person = result.minted[("person", "01QNEW527")]
    span = result.minted[("assignment", "S_NEW")]
    row = await db.fetchrow(
        "SELECT person_id, role_id, start_date, is_current FROM role_assignments WHERE id = $1",
        span,
    )
    assert dict(row) == {
        "person_id": person,
        "role_id": world["role"],
        "start_date": SECOND,
        "is_current": True,
    }
    anchored = await db.fetchval(
        "SELECT count(*) FROM producer_crosswalk WHERE source = $1 AND pm_id = ANY($2)",
        SOURCE,
        [person, span],
    )
    assert anchored == 2
