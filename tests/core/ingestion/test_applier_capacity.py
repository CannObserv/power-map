"""The diff and writer for seat capacity periods (#671): a child binding that owns its table.

`desired_role_capacity_periods` lands usa-wa's `role_periods` under each in-scope
role, matched on `valid_from` among unarchived rows; `valid_to` and `max_holders`
are owned, and a null in either is a value (an open period, no limit). The table
is the dataset's outright (`owns_rows: all`): every live period under an
in-scope role that the snapshot dropped is archived, gated as an archive, and a
role outside scope is never read.
"""

import itertools
from datetime import date

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, Diff, Entry, diff_desired  # noqa: E402
from src.core.ingestion.applier_report import verdict_for  # noqa: E402
from src.core.ingestion.applier_write import plan_statements  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

MANIFEST = load_manifest()
CAPACITY = "desired_role_capacity_periods"
R19, R20 = "01R19", "01R20"  # usa-wa role entity ids
MR19, MR20 = "01S19", "01S20"  # their PM roles
MRX = "01SX"  # a PM role no usa-wa anchor names
ANCHORED = ((R19, MR19), (R20, MR20))
Y1889, Y1891 = date(1889, 1, 1), date(1891, 1, 1)
END_1890 = date(1890, 12, 31)


def xw(producer_id, pm_id):
    return {
        "source": PRODUCER_SOURCE,
        "kind": "role",
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
    }


def period(row_id, role_id, valid_from, valid_to=None, max_holders=None, *, archived=None):
    return {
        "id": row_id,
        "role_id": role_id,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "max_holders": max_holders,
        "archived_at": archived,
    }


def claim(role_pm_id, valid_from, valid_to=None, max_holders=None, *, producer=None):
    producers = {m: r for r, m in ANCHORED}
    return {
        "pm_id": role_pm_id,
        "producer_id": producer or producers[role_pm_id],
        "valid_from": valid_from,
        "valid_to": valid_to,
        "max_holders": max_holders,
    }


def _store(*rows):
    return FakeLiveStore(
        crosswalk=[xw(r, m) for r, m in ANCHORED],
        tables={
            "roles": [{"id": m, "archived_at": None} for _, m in ANCHORED]
            + [{"id": MRX, "archived_at": None}],
            "role_capacity_periods": list(rows),
        },
    )


def _state(*claims) -> DesiredState:
    tables = {n: [] for n in MANIFEST.tables}
    tables["desired_roles"] = [{"pm_id": m, "producer_id": r} for r, m in ANCHORED]
    tables[CAPACITY] = list(claims)
    return DesiredState(tables=tables, build_info=None)


def _capacity(diff) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {}
    for e in diff.entries:
        if e.table == CAPACITY:
            out.setdefault(e.kind, []).append(e)
    return out


# --- diff -------------------------------------------------------------------------


async def test_a_published_period_pm_lacks_is_an_insert():
    diff = await diff_desired(_state(claim(MR19, Y1889, END_1890, 5)), MANIFEST, _store())

    (insert,) = _capacity(diff)["insert"]
    assert insert.entry_id == f"{CAPACITY}:{R19}|1889-01-01"
    assert insert.pm_id == MR19
    assert insert.changes == {
        "valid_from": (None, Y1889),
        "valid_to": (None, END_1890),
        "max_holders": (None, 5),
    }


async def test_a_matching_period_is_a_noop():
    store = _store(period("p1", MR19, Y1889, END_1890, 5))

    diff = await diff_desired(_state(claim(MR19, Y1889, END_1890, 5)), MANIFEST, store)

    assert set(_capacity(diff)) == {"noop"}


async def test_a_changed_capacity_is_an_update():
    store = _store(period("p1", MR19, Y1889, END_1890, 5))

    diff = await diff_desired(_state(claim(MR19, Y1889, END_1890, 3)), MANIFEST, store)

    (update,) = _capacity(diff)["update"]
    assert update.row_id == "p1" and update.changes == {"max_holders": (5, 3)}


async def test_a_null_is_a_value_an_update_writes():
    """No limit and an open end are claims: the period reopens and loses its cap."""
    store = _store(period("p1", MR19, Y1891, date(1892, 12, 31), 1))

    diff = await diff_desired(_state(claim(MR19, Y1891)), MANIFEST, store)

    (update,) = _capacity(diff)["update"]
    assert update.changes == {"valid_to": (date(1892, 12, 31), None), "max_holders": (1, None)}


async def test_a_dropped_period_under_an_in_scope_role_is_archived():
    """A re-tiled role: the old start goes, gated as an archive."""
    store = _store(period("p1", MR19, Y1889, END_1890, 5), period("p2", MR19, Y1891, None, 1))

    diff = await diff_desired(_state(claim(MR19, Y1891, None, 1)), MANIFEST, store)

    (archive,) = _capacity(diff)["archive"]
    assert archive.entry_id == f"{CAPACITY}:{R19}|1889-01-01"
    assert archive.pm_id == MR19 and archive.row_id == "p1"
    assert "absent" in archive.reason


async def test_every_period_of_a_role_the_snapshot_left_is_archived():
    """`owns_rows: all`: no type filter — the whole table under the role is the dataset's."""
    store = _store(period("p1", MR20, Y1889, END_1890, 2), period("p2", MR20, Y1891))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert sorted(e.row_id for e in _capacity(diff)["archive"]) == ["p1", "p2"]


async def test_an_archived_period_is_not_archived_again():
    store = _store(period("p1", MR19, Y1889, END_1890, 5, archived="2026-10-01T00:00:00Z"))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert _capacity(diff) == {}


async def test_a_period_under_a_role_no_anchor_names_is_invisible():
    """PM's own roles keep whatever periods a person gave them."""
    store = _store(period("p1", MRX, Y1889, END_1890, 2))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert _capacity(diff) == {}


async def test_a_dropped_period_blocks_execute_at_the_default_threshold():
    store = _store(period("p1", MR19, Y1889, END_1890, 5))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert verdict_for(diff, MANIFEST.thresholds).verdict == "blocked"


async def test_inserts_alone_are_not_gated():
    """The first run lands every published period: inserts carry no threshold."""
    diff = await diff_desired(
        _state(claim(MR19, Y1889, END_1890, 5), claim(MR19, Y1891, None, 1)), MANIFEST, _store()
    )

    assert verdict_for(diff, MANIFEST.thresholds).verdict == "clean"


# --- write ------------------------------------------------------------------------


def _plan(*entries):
    ids = (f"NEW{n}" for n in itertools.count(1))
    return plan_statements(Diff(list(entries)), MANIFEST, source=PRODUCER_SOURCE, ids=ids)


def _entry(kind, **kw):
    return Entry(
        entry_id=f"{CAPACITY}:{R19}|1889-01-01",
        table=CAPACITY,
        kind=kind,
        producer_id=R19,
        pm_id=MR19,
        **kw,
    )


def test_an_insert_writes_the_period_under_its_role():
    statements, _ = _plan(
        _entry(
            "insert",
            changes={
                "valid_from": (None, Y1889),
                "valid_to": (None, END_1890),
                "max_holders": (None, 5),
            },
        )
    )

    ((sql, args),) = [(s.sql, s.args) for s in statements]
    assert sql == (
        "INSERT INTO role_capacity_periods (id, role_id, max_holders, valid_from, valid_to)"
        " VALUES ($1, $2, $3, $4, $5)"
    )
    assert args == ("NEW1", MR19, 5, Y1889, END_1890)


def test_an_archive_archives_the_period_and_stamps_no_anchor():
    """The anchor is the role's, and the role stays; only the period goes."""
    statements, _ = _plan(_entry("archive", row_id="p1"))

    assert [(s.kind, s.table, s.sql, s.args) for s in statements] == [
        (
            "archive",
            "role_capacity_periods",
            "UPDATE role_capacity_periods SET archived_at = NOW()"
            " WHERE id = $1 AND archived_at IS NULL",
            ("p1",),
        )
    ]
