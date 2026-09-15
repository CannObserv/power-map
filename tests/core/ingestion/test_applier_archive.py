"""The diff, entity shape under `retraction: archive` (#527 step 3).

An anchored span the snapshot still carries is a `noop`. An in-scope span it no
longer carries archives its live row (`archive`). A span the applier archived
that comes back is a `restore` — unless a live row now holds its slot on the
partial identity index, which is the #424 collision and a `conflict` for a
person. What PM archived, or restored, by its own hand is reported and left
alone: curation wins without stalling the run.
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
TABLE = "desired_role_assignments"
S1, S2, S3 = (
    "01Q1|committee|c|31640|2021-22",
    "01Q1|committee|c|31640|2023-24",
    "01Q3|x|y|z|2025-26",
)
A1, A2, A3, A9 = "01RA1", "01RA2", "01RA3", "01RA9"
PERSON, ROLE = "01MPERSON", "01MROLE"
START = date(2021, 1, 11)
RETRACTED = datetime(2026, 9, 15, tzinfo=UTC)


def xa(span, pm_id, *, retracted_at=None, resolution="live"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": "assignment",
        "producer_id": span,
        "pm_id": pm_id,
        "resolution": resolution,
        "retracted_at": retracted_at,
    }


def ra(pm_id, *, archived=False, start=START, person=PERSON, role=ROLE):
    return {
        "id": pm_id,
        "archived_at": RETRACTED if archived else None,
        "person_id": person,
        "role_id": role,
        "start_date": start,
    }


def desired(span, pm_id):
    return {
        "pm_id": pm_id,
        "producer_id": span,
        "person_producer_id": "01Q1",
        "role_producer_id": "01ROLE",
        "start_date": START,
    }


def _state(*rows) -> DesiredState:
    tables = {name: [] for name in MANIFEST.tables}
    tables[TABLE] = list(rows)
    return DesiredState(tables=tables, build_info=None)


async def _entries(store, *rows):
    diff = await diff_desired(_state(*rows), MANIFEST, store)
    return {e.producer_id: e for e in diff.entries if e.table == TABLE}


async def test_an_anchored_span_the_snapshot_still_carries_is_a_noop():
    store = FakeLiveStore(crosswalk=[xa(S1, A1)], tables={"role_assignments": [ra(A1)]})

    entries = await _entries(store, desired(S1, A1))

    assert entries[S1].kind == "noop"


async def test_an_in_scope_span_absent_from_the_snapshot_archives_its_live_row():
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1), xa(S2, A2)],
        tables={"role_assignments": [ra(A1), ra(A2, start=date(2023, 1, 9))]},
    )

    entries = await _entries(store, desired(S1, A1))

    archive = entries[S2]
    assert (archive.kind, archive.pm_id) == ("archive", A2)
    assert "absent from the snapshot" in archive.reason


async def test_an_absent_span_whose_row_is_already_archived_is_a_noop():
    store = FakeLiveStore(
        crosswalk=[xa(S2, A2, retracted_at=RETRACTED)],
        tables={"role_assignments": [ra(A2, archived=True)]},
    )

    entries = await _entries(store)

    assert entries[S2].kind == "noop"


async def test_a_span_the_applier_archived_restores_when_it_comes_back():
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1, retracted_at=RETRACTED)],
        tables={"role_assignments": [ra(A1, archived=True)]},
    )

    entries = await _entries(store, desired(S1, A1))

    assert (entries[S1].kind, entries[S1].pm_id) == ("restore", A1)


async def test_a_restore_onto_a_slot_a_live_row_holds_is_a_conflict():
    """#424: the partial index freed the slot when the row archived; someone took it."""
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1, retracted_at=RETRACTED)],
        tables={"role_assignments": [ra(A1, archived=True), ra(A9)]},
    )

    entries = await _entries(store, desired(S1, A1))

    conflict = entries[S1]
    assert conflict.kind == "conflict"
    assert A9 in conflict.reason and "#424" in conflict.reason


async def test_a_holder_this_plan_archives_does_not_block_a_restore():
    """Archives are written first, so the slot is free by the time the restore runs."""
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1, retracted_at=RETRACTED), xa(S3, A3)],
        tables={"role_assignments": [ra(A1, archived=True), ra(A3)]},
    )

    entries = await _entries(store, desired(S1, A1))

    assert entries[S3].kind == "archive"
    assert entries[S1].kind == "restore"


async def test_a_row_pm_archived_is_reported_and_left_archived():
    """Not the applier's archive: curation stands, and the run is not refused (`stale`)."""
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1)], tables={"role_assignments": [ra(A1, archived=True)]}
    )

    entries = await _entries(store, desired(S1, A1))

    report = entries[S1]
    assert report.kind == "retract"
    assert "archived in PM" in report.reason


async def test_a_row_pm_restored_after_an_applier_archive_is_reported_not_rearchived():
    """A person unarchived what the applier archived; the producer still omits it."""
    store = FakeLiveStore(
        crosswalk=[xa(S2, A2, retracted_at=RETRACTED)], tables={"role_assignments": [ra(A2)]}
    )

    entries = await _entries(store)

    report = entries[S2]
    assert report.kind == "retract"
    assert "restored in PM" in report.reason


async def test_an_absent_span_whose_row_a_published_span_claims_is_not_archived():
    """Two anchors on one PM row (a merge folded them): the published one keeps it."""
    store = FakeLiveStore(
        crosswalk=[xa(S1, A1), xa(S2, A1, resolution="merged")],
        tables={"role_assignments": [ra(A1)]},
    )

    entries = await _entries(store, desired(S1, A1))

    assert S2 not in entries
    assert entries[S1].kind == "noop"


async def test_an_absent_span_whose_row_is_gone_is_stale():
    store = FakeLiveStore(crosswalk=[xa(S2, A2)], tables={"role_assignments": []})

    entries = await _entries(store)

    assert entries[S2].kind == "stale"


async def test_report_tables_still_refuse_an_archived_row_as_stale():
    """Only an archiving binding owns the difference between PM's archive and its own."""
    store = FakeLiveStore(
        crosswalk=[{**xa("01P1", "01M1"), "kind": "person"}],
        tables={"people": [{"id": "01M1", "archived_at": RETRACTED}]},
    )
    tables = {name: [] for name in MANIFEST.tables}
    tables["desired_people"] = [{"pm_id": "01M1", "producer_id": "01P1"}]

    diff = await diff_desired(DesiredState(tables=tables, build_info=None), MANIFEST, store)

    assert [e.kind for e in diff.entries if e.table == "desired_people"] == ["stale"]
