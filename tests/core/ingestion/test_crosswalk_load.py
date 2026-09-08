"""Seeding the crosswalk table from a resolved anchor export (#495).

The seed is re-runnable by construction: usa-wa regenerates the export whenever
its identity registry moves, and PM's merge history moves underneath it too, so
"load it again" has to be the ordinary operation rather than a recovery step.

The report is the deliverable, not a side effect — it is the first half of the
triage pass (#501), and the states it must not blur are the two that mean *stop*:
an anchor PM cannot resolve, and two producer entities that PM has already
merged into one.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.crosswalk import Anchor, load_anchors

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
