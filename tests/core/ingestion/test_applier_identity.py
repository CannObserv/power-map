"""The diff, identity-carrying creates and the supersession pairing (#527 step 4).

A create writes its `identity` columns in its INSERT. A reference resolves
through the live crosswalk, or to the row this run mints for it, and is `stale`
when it is neither. A create that would take a slot a live row holds on the
partial identity index is a `conflict` — unless this plan archives the holder
first, which is exactly a re-segmentation (usa-wa#289: archive + create). An
archive names the published spans on its `supersession` tuple, for triage.
"""

from datetime import UTC, date, datetime

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, Minted, diff_desired  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping.manifest import parse_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import (  # noqa: E402
    FakeLiveStore,
    manifest_with_assignments,
    raw_with_assignments,
)

MANIFEST = manifest_with_assignments()
TABLE = "desired_role_assignments"
Q, Q_NEW, R = "01Q1", "01QNEW", "01ROLE"  # producer ids: a person, a new person, a role
PM_Q, PM_R = "01MQ", "01MR"
NEW = "01Q1|seat|chamber|ld-26|2013-14"
OLD = "01Q1|seat|chamber|ld-26|2011-12"
A_OLD, A9 = "01RAOLD", "01RA9"
START = date(2013, 1, 14)
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


def ra(pm_id, *, start=START, archived=False, person=PM_Q, role=PM_R):
    return {
        "id": pm_id,
        "archived_at": ARCHIVED if archived else None,
        "person_id": person,
        "role_id": role,
        "start_date": start,
    }


def span(producer_id, *, pm_id=None, person=Q, start=START):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "person_producer_id": person,
        "role_producer_id": R,
        "start_date": start,
    }


def _state(manifest=MANIFEST, **tables) -> DesiredState:
    return DesiredState(
        tables={name: list(tables.get(name, [])) for name in manifest.tables}, build_info=None
    )


async def _diff(store, *spans, manifest=MANIFEST, **tables):
    return await diff_desired(_state(manifest, **{TABLE: spans}, **tables), manifest, store)


def _by_producer(diff, table=TABLE):
    return {e.producer_id: e for e in diff.entries if e.table == table}


async def test_a_create_writes_its_identity_resolved_through_the_live_crosswalk():
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": []})

    create = _by_producer(await _diff(store, span(NEW)))[NEW]

    assert create.kind == "create"
    assert create.changes == {
        "person_id": (None, PM_Q),
        "role_id": (None, PM_R),
        "start_date": (None, START),
    }


async def test_a_create_whose_person_is_created_this_run_names_the_minted_row():
    """A new legislator: the person and the span land in one run."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": [], "people": []})

    diff = await _diff(
        store,
        span(NEW, person=Q_NEW),
        desired_people=[{"pm_id": None, "producer_id": Q_NEW}],
    )

    create = _by_producer(diff)[NEW]
    assert create.kind == "create"
    assert create.changes["person_id"] == (None, Minted("person", Q_NEW))


async def test_references_resolve_whatever_order_the_manifest_lists_tables_in():
    """The span's table may come first; the person it names is still known as a create."""
    raw = raw_with_assignments()
    raw["tables"] = {
        TABLE: raw["tables"].pop(TABLE),
        **raw["tables"],
    }
    manifest = parse_manifest(raw)
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": [], "people": []})

    diff = await _diff(
        store,
        span(NEW, person=Q_NEW),
        manifest=manifest,
        desired_people=[{"pm_id": None, "producer_id": Q_NEW}],
    )

    assert _by_producer(diff)[NEW].changes["person_id"] == (None, Minted("person", Q_NEW))


async def test_a_create_naming_a_person_neither_anchored_nor_created_is_stale():
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": []})

    entry = _by_producer(await _diff(store, span(NEW, person="01QGHOST")))[NEW]

    assert entry.kind == "stale"
    assert "01QGHOST" in entry.reason


async def test_a_create_onto_a_slot_a_live_row_holds_is_a_conflict():
    """uq_role_assignment_person_role_start would refuse the INSERT; a person decides."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": [ra(A9)]})

    entry = _by_producer(await _diff(store, span(NEW)))[NEW]

    assert entry.kind == "conflict"
    assert A9 in entry.reason


async def test_a_create_onto_a_slot_this_plan_archives_is_a_create():
    """A re-segmentation keeps its start: the old key archives first, then the new lands."""
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", OLD, A_OLD)],
        tables={"role_assignments": [ra(A_OLD)]},
    )

    entries = _by_producer(await _diff(store, span(NEW)))

    assert entries[OLD].kind == "archive"
    assert entries[NEW].kind == "create"


async def test_a_create_where_an_archived_row_holds_the_slot_carries_a_hint():
    """The LD-1 2019 case: PM archived a row on this slot — probably the same tenure."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": [ra(A9, archived=True)]})

    create = _by_producer(await _diff(store, span(NEW)))[NEW]

    assert create.kind == "create"
    assert [h["archived_holder"] for h in create.hint] == [A9]


async def test_an_archive_names_the_published_spans_on_its_supersession_tuple():
    """usa-wa#289 collapsed a member's party spans: the later segment archives, and
    the report pairs it with the span that now covers it."""
    kept = "01Q1|party|party|republican|2011-12"
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", OLD, A_OLD), xw("assignment", kept, "01RAKEPT")],
        tables={
            "role_assignments": [
                ra(A_OLD, start=date(2011, 1, 10)),
                ra("01RAKEPT", start=date(2009, 1, 12)),
            ]
        },
    )

    entries = _by_producer(
        await _diff(store, span(kept, pm_id="01RAKEPT", start=date(2009, 1, 12)))
    )

    assert entries[OLD].kind == "archive"
    assert entries[OLD].effects == {"superseded_by": [kept]}


async def test_an_archive_with_no_published_span_on_its_tuple_names_none():
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", OLD, A_OLD)],
        tables={"role_assignments": [ra(A_OLD)]},
    )

    archive = _by_producer(await _diff(store))[OLD]

    assert archive.kind == "archive" and archive.effects == {}


async def test_an_archive_previews_the_rows_that_archive_with_it():
    """#301: the relationships on an assignment archive with it, and a restore does
    not bring them back — so the report says how many, before anything is written."""
    edge = {
        "id": "E1",
        "from_assignment_id": A_OLD,
        "to_assignment_id": "01RAX",
        "archived_at": None,
    }
    gone = {**edge, "id": "E0", "archived_at": ARCHIVED}
    store = FakeLiveStore(
        crosswalk=[*ANCHORS, xw("assignment", OLD, A_OLD)],
        tables={"role_assignments": [ra(A_OLD)], "role_assignment_relationships": [edge, gone]},
    )

    archive = _by_producer(await _diff(store))[OLD]

    assert archive.effects == {"cascades": {"role_assignment_relationships": 1}}


async def test_a_create_naming_no_role_is_stale():
    """The model keeps a span whose role is unpublished (unresolved_assignment_roles
    names it); its INSERT would break role_id NOT NULL, so it is never planned."""
    store = FakeLiveStore(crosswalk=ANCHORS, tables={"role_assignments": []})

    entry = _by_producer(await _diff(store, {**span(NEW), "role_producer_id": None}))[NEW]

    assert entry.kind == "stale"
    assert "names no role" in entry.reason
