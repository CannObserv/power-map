"""Person identifiers against a real database (#710).

The diff is proven on the fake. Here, through the seam: an execute writes a
roster key onto an anchored person and onto a person the same run creates, and
`resolve_entity` — what an observation and `identify` read — then finds each.
A key another PM person already holds is a conflict the probe sees, and the
plan re-diffs clean inside its own transaction.
"""

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402
from src.core.ingestion.applier_write import apply_diff  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from src.core.observation import Disposition, resolve_entity  # noqa: E402

pytestmark = [pytest.mark.integration]

SOURCE = "test-710"
MANIFEST = load_manifest()
IDS = "desired_person_identifiers"
ROSTER = "person_wa_legislature_roster"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def people(db):
    """A anchored; X a PM person no anchor names."""
    out = {}
    for label in "AX":
        out[label] = generate_id()
        await db.execute("INSERT INTO people (id) VALUES ($1)", out[label])
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, 'person', 'P710A', $3, $3, 'live')",
        generate_id(),
        SOURCE,
        out["A"],
    )
    return out


async def _ident(db, entity_id, value, slug=ROSTER) -> None:
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, $3 FROM entity_identifier_types t WHERE t.slug = $4",
        generate_id(),
        entity_id,
        value,
        slug,
    )


def _state(people, *claims, creates=()) -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables["desired_people"] = [{"pm_id": people["A"], "producer_id": "P710A"}] + [
        {"pm_id": None, "producer_id": p} for p in creates
    ]
    tables["desired_person_names"] = [
        {"pm_id": None, "producer_id": p, "name": f"Created {p}", "name_type": "legal"}
        for p in creates
    ]
    tables[IDS] = [
        {"pm_id": pm, "producer_id": producer, "identifier_type": ROSTER, "value": value}
        for pm, producer, value in claims
    ]
    return DesiredState(tables=tables, build_info=None)


async def _apply(db, state):
    store = PostgresLiveStore(db)
    diff = await diff_desired(state, MANIFEST, store, source=SOURCE)

    async def rediff(minted):
        return await diff_desired(state, MANIFEST, store, source=SOURCE, minted=minted)

    await apply_diff(diff, MANIFEST, db, source=SOURCE, rediff=rediff)
    return diff


async def test_an_execute_writes_keys_that_resolve_entity_then_finds(db, people):
    await _ident(db, people["A"], "smith:1933")  # held already: a noop

    diff = await _apply(
        db,
        _state(
            people,
            (people["A"], "P710A", "smith:1933"),
            (people["A"], "P710A", "smith:1941"),
            (None, "P710N", "nettie:1943"),
            creates=("P710N",),
        ),
    )

    kinds = sorted(e.kind for e in diff.entries if e.table == IDS)
    assert kinds == ["insert", "insert", "noop"]
    found, entity_type, disposition, _ = await resolve_entity(db, ROSTER, "smith:1941")
    assert (found, entity_type, disposition) == (people["A"], "person", Disposition.AUTO_ATTACHED)
    created = await db.fetchval(
        "SELECT pm_id FROM producer_crosswalk WHERE source = $1 AND producer_id = 'P710N'", SOURCE
    )
    found, _, disposition, _ = await resolve_entity(db, ROSTER, "nettie:1943")
    assert (found, disposition) == (created, Disposition.AUTO_ATTACHED)


async def test_a_key_on_another_pm_person_is_a_conflict_the_probe_sees(db, people):
    await _ident(db, people["X"], "dup:2001")

    store = PostgresLiveStore(db)
    diff = await diff_desired(
        _state(people, (people["A"], "P710A", "dup:2001")), MANIFEST, store, source=SOURCE
    )

    (entry,) = [e for e in diff.entries if e.table == IDS]
    assert entry.kind == "conflict" and people["X"] in entry.reason
