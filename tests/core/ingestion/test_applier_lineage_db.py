"""Org links against a real database (#592).

The diff is proven on the fake. Here: that an execute writes the year onto an
undated link (the Civic Health edge), inserts a published link naming the
linked org, archives a dropped one, leaves a link to an unanchored org alone —
and that the plan re-diffs clean inside its own transaction.
"""

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402
from src.core.ingestion.applier_write import apply_diff  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402

pytestmark = [pytest.mark.integration]

SOURCE = "test-592"
MANIFEST = load_manifest()
LINEAGE = "desired_organization_lineage"


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
async def orgs(db):
    """Three anchored orgs (A, B, C) and one no anchor names (D)."""
    out = {}
    for label in "ABCD":
        out[label] = generate_id()
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", out[label])
        if label != "D":
            await db.execute(
                "INSERT INTO producer_crosswalk"
                " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
                " VALUES ($1, $2, 'organization', $3, $4, $4, 'live')",
                generate_id(),
                SOURCE,
                f"P592{label}",
                out[label],
            )
    return out


async def _link(db, subject, slug, linked, year=None) -> str:
    row = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year,"
        " linked_entity_type, linked_entity_id)"
        " SELECT $1, 'organization', $2, t.id, $4, 'organization', $3"
        " FROM entity_event_types t WHERE t.slug = $5",
        row,
        subject,
        linked,
        year,
        slug,
    )
    return row


def _claim(orgs, subject, slug, linked, year):
    return {
        "pm_id": orgs[subject],
        "producer_id": f"P592{subject}",
        "event_type": slug,
        "linked_pm_id": orgs[linked],
        "linked_producer_id": f"P592{linked}",
        "event_year": year,
    }


def _state(orgs, *claims) -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables["desired_organizations"] = [{"pm_id": orgs[x], "producer_id": f"P592{x}"} for x in "ABC"]
    tables[LINEAGE] = list(claims)
    return DesiredState(tables=tables, build_info=None)


async def _apply(db, state):
    store = PostgresLiveStore(db)
    diff = await diff_desired(state, MANIFEST, store, source=SOURCE)

    async def rediff(minted):
        return await diff_desired(state, MANIFEST, store, source=SOURCE, minted=minted)

    await apply_diff(diff, MANIFEST, db, source=SOURCE, rediff=rediff)
    return diff


async def test_an_execute_dates_inserts_and_archives_links_and_rediffs_clean(db, orgs):
    undated = await _link(db, orgs["A"], "succeeded_by", orgs["B"])
    dropped = await _link(db, orgs["A"], "merged_with", orgs["C"], 2001)
    pms_own = await _link(db, orgs["A"], "split_from", orgs["D"], 2015)

    diff = await _apply(
        db,
        _state(
            orgs,
            _claim(orgs, "A", "succeeded_by", "B", 2026),
            _claim(orgs, "B", "succeeded_by", "C", 2027),
        ),
    )

    kinds = sorted(e.kind for e in diff.entries if e.table == LINEAGE)
    assert kinds == ["archive", "insert", "update"]
    assert await db.fetchval("SELECT event_year FROM entity_events WHERE id = $1", undated) == 2026
    assert await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM entity_events WHERE id = $1", dropped
    )
    assert not await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM entity_events WHERE id = $1", pms_own
    )
    inserted = await db.fetchrow(
        "SELECT e.event_year, e.linked_entity_type, e.visibility FROM entity_events e"
        " JOIN entity_event_types t ON t.id = e.event_type_id"
        " WHERE e.entity_id = $1 AND t.slug = 'succeeded_by' AND e.linked_entity_id = $2",
        orgs["B"],
        orgs["C"],
    )
    assert tuple(inserted) == (2027, "organization", "public")
    # No anchor is stamped: the orgs stay, only the link went.
    assert not await db.fetchval(
        "SELECT bool_or(retracted_at IS NOT NULL) FROM producer_crosswalk WHERE source = $1",
        SOURCE,
    )
