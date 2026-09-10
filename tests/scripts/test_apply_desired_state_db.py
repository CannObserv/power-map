"""The applier against a real database (#499 step 8).

What the fake cannot prove, proved here on the rollback connection: the
asyncpg store's SQL, the same snapshot applied twice being a no-op, a gated
create writing nothing, an execute writing exactly the predicted entries with
the outbox firing, a curator-only column surviving, a row outside the crosswalk
surviving, a cyclic parent rolling the run back, and a crosswalk change between
build and apply being stale.
"""

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from scripts import apply_desired_state as cli  # noqa: E402
from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier_report import LEDGER, read_ledger  # noqa: E402
from tests.core.ingestion.applier_fakes import write_desired  # noqa: E402

pytestmark = pytest.mark.integration

CURATED = 'A.L. "Slim" Rasmussen'
PRODUCED = "A. L. “Slim” Rasmussen"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


def _clock():
    start = datetime(2026, 9, 11, 9, 30, tzinfo=UTC)
    ticks = itertools.count()
    return lambda: start + timedelta(minutes=next(ticks))


async def _person(db, name, *, notes=None):
    pid = generate_id()
    await db.execute("INSERT INTO people (id, notes) VALUES ($1, $2)", pid, notes)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _org(db, name, *, parent=None):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", oid, parent)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _anchor(db, kind, producer_id, pm_id, resolution="live"):
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, 'usa-wa', $2, $3, $4, $5, $6)",
        generate_id(),
        kind,
        producer_id,
        pm_id,
        pm_id,
        resolution,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def world(db, tmp_path):
    """One curated person, a chamber and a committee, all anchored; one person outside."""
    marker = generate_id()[-6:]
    p1, o_house, o4 = f"P1{marker}", f"OH{marker}", f"O4{marker}"
    pm1 = await _person(db, CURATED, notes="curator's")
    pmz = await _person(db, "Outside The Crosswalk")
    mo_house = await _org(db, "Washington State House of Representatives")
    mo4 = await _org(db, "House Committee on Capital Budget")
    await _anchor(db, "person", p1, pm1)
    await _anchor(db, "organization", o_house, mo_house)
    await _anchor(db, "organization", o4, mo4)
    desired = tmp_path / "desired"
    desired.mkdir()
    (desired / "BUILD.json").write_text(json.dumps({"datasets": {"persons": "v-test"}}))
    return {
        "db": db,
        "desired": desired,
        "out": tmp_path / "applier",
        "now": _clock(),
        "p1": p1,
        "pm1": pm1,
        "pmz": pmz,
        "o_house": o_house,
        "mo_house": mo_house,
        "o4": o4,
        "mo4": mo4,
    }


def _write_world(w, **extra):
    tables = {
        "desired_people": [{"pm_id": w["pm1"], "producer_id": w["p1"]}],
        "desired_person_names": [
            {"pm_id": w["pm1"], "producer_id": w["p1"], "name": PRODUCED, "name_type": "legal"}
        ],
        "desired_organizations": [
            {"pm_id": w["mo_house"], "producer_id": w["o_house"]},
            {"pm_id": w["mo4"], "producer_id": w["o4"]},
        ],
        "desired_organization_parents": [
            {"pm_id": w["mo4"], "parent_pm_id": w["mo_house"], "producer_id": w["o4"]}
        ],
        "desired_organization_names": [
            {
                "pm_id": w["mo4"],
                "producer_id": w["o4"],
                "name": "House Committee on Capital Budget",
                "name_type": "legal",
            }
        ],
        "desired_organization_acronyms": [
            {"pm_id": w["mo4"], "producer_id": w["o4"], "acronym": "CB"}
        ],
        "desired_entity_events": [
            {
                "pm_id": w["mo4"],
                "producer_id": w["o4"],
                "entity_type": "organization",
                "event_type": "dissolved",
                "event_year": 2020,
            }
        ],
    }
    for table, rows in extra.items():
        tables[table] = rows
    write_desired(w["desired"], **tables)


async def _run(w, **kw):
    return await cli.run(w["db"], desired=w["desired"], out=w["out"], now=w["now"], **kw)


def _summary(w, n=-1):
    runs = sorted(w["out"].glob("*/summary.json"))
    return json.loads(runs[n].read_text())


async def test_a_dry_run_against_a_real_store_reports_the_measured_shapes(world):
    _write_world(world)

    code = await _run(world, execute=False)

    assert code == 0
    summary = _summary(world)
    assert summary["verdict"] == "clean"
    by = summary["by_table"]
    assert by["desired_person_names"]["update"] == 1  # the curated form is in dispute
    assert by["desired_organization_parents"]["update"] == 1  # parent NULL → the House
    assert by["desired_organization_acronyms"]["insert"] == 1  # no acronym yet
    assert by["desired_entity_events"]["insert"] == 1  # no dissolved yet
    assert by["desired_organization_names"]["noop"] == 1  # present on the canonical row
    assert by["desired_people"]["noop"] == 1 and by["desired_organizations"]["noop"] == 2


async def test_execute_writes_exactly_the_predicted_entries_and_the_outbox_fires(world):
    db = world["db"]
    _write_world(world)
    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_id = ANY($1::text[])",
        [world["pm1"], world["mo4"]],
    )
    for _ in range(3):
        assert await _run(world, execute=False) == 0

    code = await _run(world, execute=True)

    assert code == 0
    assert read_ledger(world["out"] / LEDGER)[-1]["mode"] == "execute"
    name = await db.fetchval(
        "SELECT name FROM person_names WHERE person_id = $1 AND is_canonical", world["pm1"]
    )
    assert name == PRODUCED
    assert await db.fetchval("SELECT notes FROM people WHERE id = $1", world["pm1"]) == "curator's"
    assert (
        await db.fetchval("SELECT parent_id FROM organizations WHERE id = $1", world["mo4"])
        == (world["mo_house"])
    )
    acronym = await db.fetchrow(
        "SELECT acronym, is_canonical FROM organization_acronyms WHERE organization_id = $1",
        world["mo4"],
    )
    assert (acronym["acronym"], acronym["is_canonical"]) == ("CB", True)
    year = await db.fetchval(
        "SELECT e.event_year FROM entity_events e"
        " JOIN entity_event_types t ON t.id = e.event_type_id"
        " WHERE e.entity_id = $1 AND t.slug = 'dissolved' AND e.archived_at IS NULL",
        world["mo4"],
    )
    assert year == 2020
    outside = await db.fetchval(
        "SELECT name FROM person_names WHERE person_id = $1 AND is_canonical", world["pmz"]
    )
    assert outside == "Outside The Crosswalk"
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_id = ANY($1::text[])",
        [world["pm1"], world["mo4"]],
    )
    assert after > before

    # The same snapshot again: a no-op, and a clean one.
    assert await _run(world, execute=False) == 0
    summary = _summary(world)
    assert summary["verdict"] == "clean" and summary["entries"] == 0


async def test_a_gated_create_writes_nothing_even_under_execute(world):
    db = world["db"]
    p3 = "P3" + generate_id()[-6:]
    _write_world(
        world,
        desired_people=[
            {"pm_id": world["pm1"], "producer_id": world["p1"]},
            {"pm_id": None, "producer_id": p3},
        ],
        desired_person_names=[
            {"pm_id": None, "producer_id": p3, "name": "Nobody Yet", "name_type": "legal"}
        ],
    )
    people_before = await db.fetchval("SELECT count(*) FROM people")

    assert await _run(world, execute=False) == 0
    assert _summary(world)["verdict"] == "blocked"
    code = await _run(world, execute=True)

    assert code == 1
    assert await db.fetchval("SELECT count(*) FROM people") == people_before
    assert (
        await db.fetchval("SELECT count(*) FROM producer_crosswalk WHERE producer_id = $1", p3) == 0
    )


async def test_an_allowed_create_lands_with_its_name_and_crosswalk_row(world):
    db = world["db"]
    p3 = "P3" + generate_id()[-6:]
    _write_world(
        world,
        desired_people=[{"pm_id": None, "producer_id": p3}],
        desired_person_names=[
            {"pm_id": None, "producer_id": p3, "name": "Emily Alvarado", "name_type": "legal"}
        ],
        desired_organization_parents=[],
        desired_organization_acronyms=[],
        desired_entity_events=[],
    )
    thresholds = cli.thresholds_with(allow_creates=1)
    for _ in range(3):
        assert await _run(world, execute=False, thresholds=thresholds) == 0

    code = await _run(world, execute=True, thresholds=thresholds)

    assert code == 0
    row = await db.fetchrow(
        "SELECT pm_id, exported_pm_id, resolution FROM producer_crosswalk"
        " WHERE source = 'usa-wa' AND kind = 'person' AND producer_id = $1",
        p3,
    )
    assert row["resolution"] == "live" and row["pm_id"] == row["exported_pm_id"]
    name = await db.fetchrow(
        "SELECT name, is_canonical, visibility FROM person_names WHERE person_id = $1", row["pm_id"]
    )
    assert (name["name"], name["is_canonical"], name["visibility"]) == (
        "Emily Alvarado",
        True,
        "public",
    )


async def test_a_cyclic_parent_rolls_the_run_back(world):
    """The House under the committee while the committee goes under the House."""
    db = world["db"]
    await db.execute(
        "UPDATE organizations SET parent_id = $1 WHERE id = $2", world["mo_house"], world["mo4"]
    )
    _write_world(
        world,
        desired_organization_parents=[
            {
                "pm_id": world["mo_house"],
                "parent_pm_id": world["mo4"],
                "producer_id": world["o_house"],
            }
        ],
        desired_person_names=[],
        desired_organization_acronyms=[],
        desired_entity_events=[],
    )
    for _ in range(3):
        assert await _run(world, execute=False) == 0

    code = await _run(world, execute=True)

    assert code == 1
    assert read_ledger(world["out"] / LEDGER)[-1]["verdict"] == "rolled_back"
    parent = await db.fetchval(
        "SELECT parent_id FROM organizations WHERE id = $1", world["mo_house"]
    )
    assert parent is None


async def test_a_crosswalk_change_between_build_and_apply_is_stale(world):
    db = world["db"]
    _write_world(world)
    await db.execute(
        "UPDATE producer_crosswalk SET resolution = 'archived' WHERE producer_id = $1", world["p1"]
    )

    code = await _run(world, execute=False)

    assert code == 3
    assert _summary(world)["verdict"] == "stale"
