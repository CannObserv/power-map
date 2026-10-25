"""Seat capacity and membership against a real database (#671).

The diff is proven on the fake. Here: that an execute inserts published periods
under an anchored role (a null limit and an open end landing as NULL), updates a
changed one, archives a dropped one, leaves a period under an unanchored role
alone, marks a substitute — and that the plan re-diffs clean inside its own
transaction, past the table's CHECKs and its partial unique index.
"""

from datetime import date

import pytest
import pytest_asyncio

pytest.importorskip("duckdb")

from src.core.db import generate_id  # noqa: E402
from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_pg import PostgresLiveStore  # noqa: E402
from src.core.ingestion.applier_write import apply_diff  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402

pytestmark = [pytest.mark.integration]

SOURCE = "test-671"
MANIFEST = load_manifest()
CAPACITY = "desired_role_capacity_periods"
MEMBERSHIP = "desired_role_assignment_membership"
SPAN = "P671|seat:senate:ld-22|chamber-senate|22|1991-92"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _anchor(db, kind, producer_id, pm_id):
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $5, 'live')",
        generate_id(),
        SOURCE,
        kind,
        producer_id,
        pm_id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def world(db):
    """Two anchored roles (A, B), one no anchor names (X), and an anchored span on A."""
    org = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    out = {"org": org}
    for label in "ABX":
        out[label] = generate_id()
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
            out[label],
            org,
            f"Seat {label}",
        )
        if label != "X":
            await _anchor(db, "role", f"R671{label}", out[label])
    out["person"], out["ra"] = generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", out["person"])
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date)"
        " VALUES ($1, $2, $3, '1991-01-14', '1991-04-18')",
        out["ra"],
        out["person"],
        out["A"],
    )
    await _anchor(db, "person", "P671", out["person"])
    await _anchor(db, "assignment", SPAN, out["ra"])
    return out


async def _period(db, role_id, valid_from, valid_to, max_holders) -> str:
    row = generate_id()
    await db.execute(
        "INSERT INTO role_capacity_periods (id, role_id, valid_from, valid_to, max_holders)"
        " VALUES ($1, $2, $3, $4, $5)",
        row,
        role_id,
        valid_from,
        valid_to,
        max_holders,
    )
    return row


def _state(w, *periods, membership="principal") -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables["desired_roles"] = [{"pm_id": w[x], "producer_id": f"R671{x}"} for x in "AB"]
    tables["desired_people"] = [{"pm_id": w["person"], "producer_id": "P671"}]
    tables["desired_role_assignments"] = [
        {
            "pm_id": w["ra"],
            "producer_id": SPAN,
            "person_producer_id": "P671",
            "role_producer_id": "R671A",
            "start_date": date(1991, 1, 14),
        }
    ]
    tables[MEMBERSHIP] = [{"pm_id": w["ra"], "producer_id": SPAN, "membership_type": membership}]
    tables[CAPACITY] = [
        {
            "pm_id": w[x],
            "producer_id": f"R671{x}",
            "valid_from": vf,
            "valid_to": vt,
            "max_holders": m,
        }
        for x, vf, vt, m in periods
    ]
    return DesiredState(tables=tables, build_info=None)


async def _apply(db, state):
    store = PostgresLiveStore(db)
    diff = await diff_desired(state, MANIFEST, store, source=SOURCE)

    async def rediff(minted):
        return await diff_desired(state, MANIFEST, store, source=SOURCE, minted=minted)

    await apply_diff(diff, MANIFEST, db, source=SOURCE, rediff=rediff)
    return diff


async def test_an_execute_lands_capacity_and_membership_and_rediffs_clean(db, world):
    w = world
    changed = await _period(db, w["B"], date(1889, 1, 1), date(1890, 12, 31), 5)
    dropped = await _period(db, w["B"], date(1891, 1, 1), None, 1)
    pms_own = await _period(db, w["X"], date(1889, 1, 1), None, 2)

    diff = await _apply(
        db,
        _state(
            w,
            ("A", date(1889, 1, 1), date(1890, 12, 31), 3),
            ("A", date(1891, 1, 1), None, 1),
            ("B", date(1889, 1, 1), date(1890, 12, 31), 3),
            ("B", date(1889, 6, 1), None, None),  # a re-tiled start, no limit
            membership="substitute",
        ),
    )

    kinds = sorted(e.kind for e in diff.entries if e.table == CAPACITY)
    assert kinds == ["archive", "insert", "insert", "insert", "update"]
    rows = await db.fetch(
        "SELECT role_id, valid_from, valid_to, max_holders FROM role_capacity_periods"
        " WHERE role_id = ANY($1::text[]) AND archived_at IS NULL ORDER BY role_id, valid_from",
        [w["A"], w["B"]],
    )
    assert sorted(tuple(r) for r in rows) == sorted(
        [
            (w["A"], date(1889, 1, 1), date(1890, 12, 31), 3),
            (w["A"], date(1891, 1, 1), None, 1),
            (w["B"], date(1889, 1, 1), date(1890, 12, 31), 3),
            (w["B"], date(1889, 6, 1), None, None),
        ]
    )
    assert (
        await db.fetchval("SELECT max_holders FROM role_capacity_periods WHERE id = $1", changed)
        == 3
    )
    assert await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM role_capacity_periods WHERE id = $1", dropped
    )
    assert not await db.fetchval(
        "SELECT archived_at IS NOT NULL FROM role_capacity_periods WHERE id = $1", pms_own
    )
    assert (
        await db.fetchval("SELECT membership_type FROM role_assignments WHERE id = $1", w["ra"])
        == "substitute"
    )
    # No anchor is stamped: the roles stay, only a period went.
    assert not await db.fetchval(
        "SELECT bool_or(retracted_at IS NOT NULL) FROM producer_crosswalk WHERE source = $1",
        SOURCE,
    )
