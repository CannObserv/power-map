"""The diff, the assignment dates binding (#527 step 5).

`desired_role_assignment_dates` is a column binding on `role_assignments`
owning `start_date`, `end_date` and `is_current`. Its `asserts_null` makes a
null `end_date` an instruction — an open span reopens PM's row (usa-wa#289) —
where every other null stays silence (CR 5). A `start_date` that moves is a
move on the partial identity index and is checked like a create. On a row PM
archived the dates are skipped, since the entity binding already reported it;
on a row this run restores they are compared; on a row this run creates they
leave out what the create's INSERT already wrote.
"""

from datetime import UTC, date, datetime

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from tests.core.ingestion.applier_fakes import (  # noqa: E402
    FakeLiveStore,
    manifest_with_assignments,
)

MANIFEST = manifest_with_assignments()
SPANS, DATES = "desired_role_assignments", "desired_role_assignment_dates"
Q, R, PM_Q, PM_R = "01Q1", "01ROLE", "01MQ", "01MR"
S1, S2, S9, S_NEW = (
    "01Q1|p|p|r|2021-22",
    "01Q1|p|p|r|2023-24",
    "01Q1|p|p|r|2019-20",
    "01Q1|n|n|n|2025-26",
)
A1, A2, A9 = "01RA1", "01RA2", "01RA9"
JAN = date(2021, 1, 11)
ARCHIVED = datetime(2026, 9, 1, tzinfo=UTC)


def xw(kind, producer_id, pm_id, **kw):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
        **kw,
    }


ANCHORS = [xw("person", Q, PM_Q), xw("role", R, PM_R)]


def ra(pm_id, *, start=JAN, end=None, current=True, archived=False):
    return {
        "id": pm_id,
        "archived_at": ARCHIVED if archived else None,
        "person_id": PM_Q,
        "role_id": PM_R,
        "start_date": start,
        "end_date": end,
        "is_current": current,
    }


def span(producer_id, pm_id, *, start=JAN):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "person_producer_id": Q,
        "role_producer_id": R,
        "start_date": start,
    }


def dates(producer_id, pm_id, *, start=JAN, end=None, current=None):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "start_date": start,
        "end_date": end,
        "is_current": end is None if current is None else current,
    }


async def _diff(store, *, spans=(), rows=()):
    tables = {name: [] for name in MANIFEST.tables}
    tables[SPANS], tables[DATES] = list(spans), list(rows)
    return await diff_desired(DesiredState(tables=tables, build_info=None), MANIFEST, store)


def _dates(diff):
    return {e.producer_id: e for e in diff.entries if e.table == DATES}


async def test_an_open_span_clears_pm_end_date_and_makes_it_current():
    """usa-wa#289: PM's first segment ended at the seat change; the party span did not."""
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1)],
        tables={"role_assignments": [ra(A1, end=date(2022, 12, 31), current=False)]},
    )

    entry = _dates(await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1)]))[S1]

    assert entry.kind == "update"
    assert entry.changes == {
        "end_date": (date(2022, 12, 31), None),
        "is_current": (False, True),
    }


async def test_a_null_in_a_column_it_does_not_assert_is_still_silence():
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1)], tables={"role_assignments": [ra(A1)]}
    )

    entry = _dates(await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1, start=None)]))[S1]

    assert entry.kind == "noop"


async def test_a_start_date_moving_onto_a_slot_a_live_row_holds_is_a_conflict():
    later = date(2021, 6, 1)
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1)],
        tables={"role_assignments": [ra(A1), ra(A9, start=later)]},
    )

    entry = _dates(await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1, start=later)]))[S1]

    assert entry.kind == "conflict"
    assert A9 in entry.reason


async def test_a_start_date_moving_onto_a_slot_this_plan_archives_is_an_update():
    later = date(2021, 6, 1)
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1), xw("assignment", S9, A9)],
        tables={"role_assignments": [ra(A1), ra(A9, start=later)]},
    )

    diff = await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1, start=later)])

    assert _dates(diff)[S1].kind == "update"
    assert {e.producer_id: e.kind for e in diff.entries if e.table == SPANS}[S9] == "archive"


async def test_a_start_date_moving_onto_the_slot_a_restore_takes_is_a_conflict():
    """Restores are written before columns, so the slot is taken by then (CR 2)."""
    later = date(2021, 6, 1)
    store = FakeLiveStore(
        crosswalk=[
            *ANCHORS,
            xw("assignment", S1, A1),
            xw("assignment", S9, A9, retracted_at=ARCHIVED),
        ],
        tables={"role_assignments": [ra(A1), ra(A9, start=later, archived=True)]},
    )

    diff = await _diff(
        store,
        spans=[span(S1, A1), span(S9, A9, start=later)],
        rows=[dates(S1, A1, start=later), dates(S9, A9, start=later)],
    )

    assert {e.producer_id: e.kind for e in diff.entries if e.table == SPANS}[S9] == "restore"
    entry = _dates(diff)[S1]
    assert entry.kind == "conflict"
    assert f"the restore of {A9}" in entry.reason


async def test_two_rows_moving_onto_one_slot_both_conflict():
    later = date(2021, 6, 1)
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1), xw("assignment", S2, A2)],
        tables={"role_assignments": [ra(A1), ra(A2, start=date(2023, 1, 9))]},
    )

    entries = _dates(
        await _diff(
            store,
            spans=[span(S1, A1), span(S2, A2, start=date(2023, 1, 9))],
            rows=[dates(S1, A1, start=later), dates(S2, A2, start=later)],
        )
    )

    assert (entries[S1].kind, entries[S2].kind) == ("conflict", "conflict")


async def test_dates_on_a_row_pm_archived_are_skipped_not_stale():
    """The entity binding reports it; the run is not refused over PM's own archive."""
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1)],
        tables={"role_assignments": [ra(A1, end=date(2022, 12, 31), current=False, archived=True)]},
    )

    diff = await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1)])

    assert _dates(diff) == {}
    assert [e.kind for e in diff.entries if e.table == SPANS] == ["retract"]


async def test_dates_on_a_row_this_run_restores_are_compared():
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", S1, A1, retracted_at=ARCHIVED)],
        tables={"role_assignments": [ra(A1, end=date(2022, 12, 31), current=False, archived=True)]},
    )

    diff = await _diff(store, spans=[span(S1, A1)], rows=[dates(S1, A1)])

    assert [e.kind for e in diff.entries if e.table == SPANS] == ["restore"]
    assert _dates(diff)[S1].changes == {
        "end_date": (date(2022, 12, 31), None),
        "is_current": (False, True),
    }


async def test_dates_on_a_row_this_run_creates_leave_out_what_the_insert_wrote():
    """The create's INSERT carries start_date (the index needs it); an open span's null
    end_date is the column's default already. What is left is the one real write."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": []})

    diff = await _diff(
        store,
        spans=[span(S_NEW, None, start=date(2025, 1, 13))],
        rows=[dates(S_NEW, None, start=date(2025, 1, 13))],
    )

    entry = _dates(diff)[S_NEW]
    assert entry.kind == "update"
    assert entry.changes == {"is_current": (None, True)}


async def test_dates_on_a_create_that_is_not_planned_point_at_its_entry():
    """The span's create is a conflict, so its dates are `stale` — and say where to look,
    since a rebuild would not help (CR 5)."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": [ra(A9)]})

    diff = await _diff(store, spans=[span(S_NEW, None)], rows=[dates(S_NEW, None)])

    assert {e.producer_id: e.kind for e in diff.entries if e.table == SPANS}[S_NEW] == "conflict"
    entry = _dates(diff)[S_NEW]
    assert entry.kind == "stale"
    assert f"see its {SPANS} entry" in entry.reason
