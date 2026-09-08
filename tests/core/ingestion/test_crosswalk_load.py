"""Seeding the crosswalk table from a resolved anchor export (#495).

The seed is re-runnable by construction: usa-wa regenerates the export whenever
its identity registry moves, and PM's merge history moves underneath it too, so
"load it again" has to be the ordinary operation rather than a recovery step.

The report is the deliverable, not a side effect — it is the first half of the
triage pass (#501), and the states it must not blur are the two that mean *stop*:
an anchor PM cannot resolve, and two producer entities that PM has already
merged into one.
"""

from datetime import date

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.crosswalk import Anchor, Supersession, load_anchors

pytestmark = [pytest.mark.integration]

SOURCE = "usa_wa"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _row(db, producer_id: str):
    return await db.fetchrow(
        "SELECT * FROM producer_crosswalk WHERE source = $1 AND producer_id = $2",
        SOURCE,
        producer_id,
    )


async def test_loads_an_anchor_with_its_resolution(db):
    pm_id = await _person(db)
    anchor = Anchor("person", generate_id(), pm_id)

    await load_anchors(db, SOURCE, [anchor], execute=True)

    row = await _row(db, anchor.producer_id)
    assert (row["kind"], row["exported_pm_id"], row["pm_id"], row["resolution"]) == (
        "person",
        pm_id,
        pm_id,
        "live",
    )


async def test_dry_run_writes_nothing(db):
    """House rule #402: a script's default must not touch the database."""
    anchor = Anchor("person", generate_id(), await _person(db))

    report = await load_anchors(db, SOURCE, [anchor], execute=False)

    assert await _row(db, anchor.producer_id) is None
    assert report.counts["live"] == 1


async def test_reloading_the_same_export_is_a_no_op(db):
    anchor = Anchor("person", generate_id(), await _person(db))

    await load_anchors(db, SOURCE, [anchor], execute=True)
    await load_anchors(db, SOURCE, [anchor], execute=True)

    assert (
        await db.fetchval(
            "SELECT count(*) FROM producer_crosswalk WHERE source=$1 AND producer_id=$2",
            SOURCE,
            anchor.producer_id,
        )
        == 1
    )


async def test_a_later_merge_updates_the_stored_resolution(db):
    """The export is unchanged; PM's merge history moved. Re-running must follow it."""
    survivor = await _person(db)
    loser = generate_id()
    anchor = Anchor("person", generate_id(), loser)

    await load_anchors(db, SOURCE, [anchor], execute=True)
    assert (await _row(db, anchor.producer_id))["resolution"] == "missing"

    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('person',$1,$2)",
        loser,
        survivor,
    )
    await load_anchors(db, SOURCE, [anchor], execute=True)

    row = await _row(db, anchor.producer_id)
    assert (row["resolution"], row["pm_id"], row["exported_pm_id"]) == ("merged", survivor, loser)


async def test_the_report_counts_every_status(db):
    live = Anchor("person", generate_id(), await _person(db))
    gone = Anchor("person", generate_id(), generate_id())

    report = await load_anchors(db, SOURCE, [live, gone], execute=True)

    assert report.counts == {"live": 1, "missing": 1}


async def test_the_report_lists_the_unresolvable_anchors(db):
    gone = Anchor("person", generate_id(), generate_id())
    dropped_id = generate_id()
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('person',$1,NULL)",
        dropped_id,
    )
    dropped = Anchor("person", generate_id(), dropped_id)

    report = await load_anchors(
        db,
        SOURCE,
        [
            gone,
            dropped,
        ],
        execute=True,
    )

    assert {(a.anchor.producer_id, a.status) for a in report.unresolved} == {
        (gone.producer_id, "missing"),
        (dropped.producer_id, "deleted_no_successor"),
    }
    assert report.is_blocking


async def test_two_producer_ids_resolving_to_one_pm_row_are_reported_as_a_collision(db):
    """PM merged what the producer still holds apart — the applier would fight itself."""
    survivor = await _person(db)
    loser = generate_id()
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ('person',$1,$2)",
        loser,
        survivor,
    )
    kept = Anchor("person", generate_id(), survivor)
    merged_away = Anchor("person", generate_id(), loser)

    report = await load_anchors(db, SOURCE, [kept, merged_away], execute=True)

    assert report.collisions == {("person", survivor): [kept.producer_id, merged_away.producer_id]}
    assert report.is_blocking


async def test_a_clean_export_does_not_block(db):
    report = await load_anchors(
        db, SOURCE, [Anchor("person", generate_id(), await _person(db))], execute=True
    )

    assert report.unresolved == []
    assert not report.is_blocking


async def _role(db) -> tuple[str, str]:
    org_id, role_id = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Member')", role_id, org_id
    )
    return org_id, role_id


async def _assignment(db, person_id, role_id, start, end, *, archived: bool):
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date)"
        " VALUES ($1,$2,$3,$4,$5)",
        aid,
        person_id,
        role_id,
        start,
        end,
    )
    if archived:
        await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", aid)
    return aid


async def test_an_archived_assignment_with_a_live_sibling_is_reported_as_superseded(db):
    """PM's dup audit archives the narrow span and keeps a deepened one under a NEW ULID.

    That is a merge in everything but name — and `archived_at` writes no
    `deleted_entities` row, so the merge-chain walk cannot see it. The anchor
    resolves `archived` and stops, pointing at a row the applier must not write
    to, while the row the producer's data actually describes is out of scope.
    Detecting it is the only way the triage pass gets a worklist.
    """
    person = await _person(db)
    _, role = await _role(db)
    superseded = await _assignment(
        db, person, role, date(1991, 1, 1), date(1992, 12, 31), archived=True
    )
    survivor = await _assignment(
        db, person, role, date(1985, 1, 1), date(1992, 12, 31), archived=False
    )
    anchor = Anchor("assignment", generate_id(), superseded)

    report = await load_anchors(db, SOURCE, [anchor], execute=True)

    assert report.supersessions == [Supersession(anchor, superseded, [survivor])]


async def test_an_archived_assignment_with_no_live_sibling_is_not_a_supersession(db):
    """A genuine archive — nothing absorbed it, and the triage answer is different."""
    person = await _person(db)
    _, role = await _role(db)
    archived = await _assignment(
        db, person, role, date(1991, 1, 1), date(1992, 12, 31), archived=True
    )

    report = await load_anchors(
        db, SOURCE, [Anchor("assignment", generate_id(), archived)], execute=True
    )

    assert report.supersessions == []
    assert report.counts == {"archived": 1}


async def test_a_live_anchor_is_never_a_supersession_candidate(db):
    """Only an archived anchor can have been superseded; a live one is just in scope."""
    person = await _person(db)
    _, role = await _role(db)
    live = await _assignment(db, person, role, date(1991, 1, 1), date(1992, 12, 31), archived=False)
    await _assignment(db, person, role, date(1985, 1, 1), date(1992, 12, 31), archived=False)

    report = await load_anchors(
        db, SOURCE, [Anchor("assignment", generate_id(), live)], execute=True
    )

    assert report.supersessions == []


async def test_an_anchor_dropped_from_a_later_export_is_reported_as_stale(db):
    """The seed is re-runnable, so an export that shrinks is an ordinary event.

    The row stays — retiring it is triage's decision, not the seed's — but an
    unreported stale row keeps a retired producer id inside the applier's scope
    indefinitely, which is the one thing the scope must not do quietly.
    """
    kept = Anchor("person", generate_id(), await _person(db))
    dropped = Anchor("person", generate_id(), await _person(db))
    await load_anchors(db, SOURCE, [kept, dropped], execute=True)

    report = await load_anchors(db, SOURCE, [kept], execute=True)

    assert report.stale == [("person", dropped.producer_id)]
    assert await _row(db, dropped.producer_id) is not None


async def test_another_source_is_not_stale(db):
    """Scope is per producer: one source's export says nothing about another's."""
    mine = Anchor("person", generate_id(), await _person(db))
    theirs = Anchor("person", generate_id(), await _person(db))
    await load_anchors(db, "observo", [theirs], execute=True)

    report = await load_anchors(db, SOURCE, [mine], execute=True)

    assert report.stale == []


async def test_a_supersession_names_the_producer_id_that_hit_it(db):
    """#501 works from producer ids; a report keyed only by PM id makes them re-join."""
    person = await _person(db)
    _, role = await _role(db)
    superseded = await _assignment(
        db, person, role, date(1991, 1, 1), date(1992, 12, 31), archived=True
    )
    survivor = await _assignment(
        db, person, role, date(1985, 1, 1), date(1992, 12, 31), archived=False
    )
    anchor = Anchor("assignment", generate_id(), superseded)

    report = await load_anchors(db, SOURCE, [anchor], execute=True)

    assert report.supersessions == [Supersession(anchor, superseded, [survivor])]


async def test_stale_detection_is_reported_as_skipped_when_the_table_is_absent(db):
    """A dry run before the schema lands is the point of a dry run.

    It still reads the live entity tables, so its resolution is real — but it
    cannot see a crosswalk that does not exist yet. Saying so is the difference
    between a check that passed and a check that never ran.
    """
    await db.execute("DROP TABLE producer_crosswalk")

    report = await load_anchors(
        db, SOURCE, [Anchor("person", generate_id(), await _person(db))], execute=False
    )

    assert report.stale_checked is False
    assert report.stale == []
    assert report.counts == {"live": 1}
