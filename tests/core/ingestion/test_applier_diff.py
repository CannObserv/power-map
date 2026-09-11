"""The diff, entity and column shapes (#499 step 3).

An `entity` row is identity: the live row exists and is unarchived (`noop`),
or PM has no row and the producer's row is a `create` — carrying a hint when
PM already holds the asserted name under another parent — or the desired
state is `stale`. Absence from the snapshot under `retraction: report` is a
`retract` entry that nothing acts on. A `column` row compares one owned
column and is an `update` on difference.
"""

import dataclasses

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import (  # noqa: E402
    ApplierError,
    DesiredState,
    diff_desired,
)
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

P1, P2, P3 = "01P1", "01P2", "01P3"
PM1, PM2, PMX, PMZ = "01M1", "01M2", "01MX", "01MZ"
O2, O4, O11 = "01O2", "01O4", "01O11"
MO2, MO4, MO_OLD = "01N2", "01N4", "01NOLD"

MANIFEST = load_manifest()
PERSON_P1 = {"pm_id": PM1, "producer_id": P1}
ORG_O2, ORG_O4 = {"pm_id": MO2, "producer_id": O2}, {"pm_id": MO4, "producer_id": O4}


def xw(producer_id, pm_id, resolution="live", *, kind="person"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": resolution,
    }


def live(pm_id, **cols):
    return {"id": pm_id, "archived_at": None, **cols}


def _state(**tables) -> DesiredState:
    """A desired state holding the given tables; every other manifest table is empty."""
    return DesiredState(
        tables={name: list(tables.get(name, [])) for name in MANIFEST.tables}, build_info=None
    )


def _kinds(diff) -> dict[str, int]:
    return {k: v for k, v in diff.counts.items() if v}


async def _people(store, *rows):
    return await diff_desired(_state(desired_people=list(rows)), MANIFEST, store)


async def test_an_anchored_entity_pm_holds_is_a_noop():
    store = FakeLiveStore(crosswalk=[xw(P1, PM1)], tables={"people": [live(PM1, notes="c")]})

    diff = await _people(store, PERSON_P1)

    assert _kinds(diff) == {"noop": 1}
    assert diff.entries[0].entry_id == "desired_people:01P1"


async def test_a_row_the_crosswalk_names_but_pm_lacks_is_stale():
    store = FakeLiveStore(crosswalk=[xw(P1, PM1)], tables={"people": []})

    diff = await _people(store, PERSON_P1)

    assert _kinds(diff) == {"stale": 1}
    assert "missing" in diff.entries[0].reason


async def test_an_archived_live_row_is_stale():
    archived = {"id": PM1, "archived_at": "2026-09-01T00:00:00Z"}
    store = FakeLiveStore(crosswalk=[xw(P1, PM1)], tables={"people": [archived]})

    diff = await _people(store, PERSON_P1)

    assert _kinds(diff) == {"stale": 1}
    assert "archived" in diff.entries[0].reason


async def test_a_create_carries_a_hint_when_pm_already_holds_the_asserted_name():
    """13 of the 17 real creates share a legal name with a person PM holds — probable twins."""
    held = {"id": "n1", "person_id": PMX, "name": "Patty Murray", "name_type": "legal"}
    store = FakeLiveStore(tables={"people": [live(PMX)], "person_names": [held]})
    state = _state(
        desired_people=[{"pm_id": None, "producer_id": P3}],
        desired_person_names=[
            {"pm_id": None, "producer_id": P3, "name": "Patty Murray", "name_type": "legal"}
        ],
    )

    diff = await diff_desired(state, MANIFEST, store)

    create = diff.by_kind("create")[0]
    assert create.entry_id == "desired_people:01P3" and create.pm_id is None
    assert create.hint == (
        {"table": "person_names", "column": "name", "value": "Patty Murray", "parent": PMX},
    )


async def test_a_create_without_a_match_has_no_hint():
    store = FakeLiveStore(tables={"people": [], "person_names": []})
    state = _state(
        desired_people=[{"pm_id": None, "producer_id": P3}],
        desired_person_names=[
            {"pm_id": None, "producer_id": P3, "name": "Nobody Known", "name_type": "legal"}
        ],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert diff.by_kind("create")[0].hint == ()


async def test_an_in_scope_row_absent_from_the_snapshot_is_a_retract_report():
    store = FakeLiveStore(
        crosswalk=[xw(P1, PM1), xw(P2, PM2)],
        tables={"people": [live(PM1), live(PM2)]},
    )

    diff = await _people(store, PERSON_P1)

    assert _kinds(diff) == {"noop": 1, "retract": 1}
    retract = diff.by_kind("retract")[0]
    assert retract.producer_id == P2 and retract.pm_id == PM2
    assert "report" in retract.reason


async def test_a_stale_row_is_not_also_reported_as_retracted():
    """Presence is judged on the whole table, not on the rows scope agreed with."""
    store = FakeLiveStore(crosswalk=[xw(P1, "01M9", "merged")], tables={"people": []})

    diff = await _people(store, PERSON_P1)

    assert _kinds(diff) == {"stale": 1}


async def test_retraction_none_reports_nothing_for_an_absent_row():
    """A parent claim absent is silence (manifest: retraction none), not a retraction."""
    store = FakeLiveStore(
        crosswalk=[xw(O2, MO2, kind="organization"), xw(O4, MO4, kind="organization")],
        tables={"organizations": [live(MO2, parent_id=None), live(MO4, parent_id=MO2)]},
    )
    state = _state(
        desired_organizations=[ORG_O2, ORG_O4],
        desired_organization_parents=[{"pm_id": MO4, "parent_pm_id": MO2, "producer_id": O4}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 3}


async def test_rows_outside_the_crosswalk_are_never_read():
    """The acceptance proof at the read: PMZ has no crosswalk row and is never requested."""
    store = FakeLiveStore(crosswalk=[xw(P1, PM1)], tables={"people": [live(PM1), live(PMZ)]})

    diff = await _people(store, PERSON_P1)

    for request in store.requested:
        if request[0] == "entity_rows":
            assert PMZ not in request[2]
    assert PMZ not in {e.pm_id for e in diff.entries}


async def test_a_column_that_already_matches_is_a_noop_and_a_differing_one_an_update():
    store = FakeLiveStore(
        crosswalk=[xw(O2, MO2, kind="organization"), xw(O4, MO4, kind="organization")],
        tables={
            "organizations": [
                live(MO2, parent_id=None, notes="x"),
                live(MO4, parent_id=MO_OLD, notes="y"),
            ]
        },
    )
    state = _state(
        desired_organizations=[ORG_O2, ORG_O4],
        desired_organization_parents=[{"pm_id": MO4, "parent_pm_id": MO2, "producer_id": O4}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    update = diff.by_kind("update")[0]
    assert update.entry_id == "desired_organization_parents:01O4"
    assert update.pm_id == MO4
    assert update.changes == {"parent_id": (MO_OLD, MO2)}


async def test_a_null_owned_value_is_no_claim_and_never_nulls_the_live_column():
    """CR 5: a present row carrying a null was doing what the manifest promises absence
    will not — `retraction: none`, an absent row is silence, not a null parent. The
    mart filters nulls (desired_organization_parents.sql), so the engine was being
    saved by its one caller; the engine is the generic half."""
    store = FakeLiveStore(
        crosswalk=[xw(O4, MO4, kind="organization")],
        tables={"organizations": [live(MO4, parent_id=MO_OLD)]},
    )
    state = _state(
        desired_organizations=[ORG_O4],
        desired_organization_parents=[{"pm_id": MO4, "parent_pm_id": None, "producer_id": O4}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 2}


async def test_a_null_owned_value_on_a_created_row_writes_nothing():
    store = FakeLiveStore(
        crosswalk=[xw(O2, MO2, kind="organization")],
        tables={"organizations": [live(MO2, parent_id=None)]},
    )
    state = _state(
        desired_organizations=[ORG_O2, {"pm_id": None, "producer_id": O11}],
        desired_organization_parents=[{"pm_id": None, "parent_pm_id": None, "producer_id": O11}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 2, "create": 1}


async def test_a_column_read_asks_for_the_bound_column_only():
    store = FakeLiveStore(
        crosswalk=[xw(O4, MO4, kind="organization")],
        tables={"organizations": [live(MO4, parent_id=MO2)]},
    )
    state = _state(
        desired_organizations=[ORG_O4],
        desired_organization_parents=[{"pm_id": MO4, "parent_pm_id": MO2, "producer_id": O4}],
    )

    await diff_desired(state, MANIFEST, store)

    asked = [r[3] for r in store.requested if r[0] == "entity_rows" and r[1] == "organizations"]
    assert ("parent_id",) in asked
    assert all(set(c) <= {"parent_id"} for c in asked)


async def test_a_column_on_a_created_row_is_an_update_pending_the_create():
    store = FakeLiveStore(
        crosswalk=[xw(O2, MO2, kind="organization")],
        tables={"organizations": [live(MO2, parent_id=None)]},
    )
    state = _state(
        desired_organizations=[ORG_O2, {"pm_id": None, "producer_id": O11}],
        desired_organization_parents=[{"pm_id": None, "parent_pm_id": MO2, "producer_id": O11}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 1, "create": 1, "update": 1}
    update = diff.by_kind("update")[0]
    assert update.pm_id is None and update.producer_id == O11
    assert update.changes == {"parent_id": (None, MO2)}
    assert "create" in update.reason


async def test_a_merge_row_is_a_report_entry():
    row = {
        "loser_pm_id": PM1,
        "survivor_pm_id": PM2,
        "loser_producer_id": P1,
        "survivor_producer_id": P2,
    }
    store = FakeLiveStore(tables={"people": []})

    diff = await diff_desired(_state(desired_person_merges=[row]), MANIFEST, store)

    merge = diff.by_kind("merge")[0]
    assert merge.entry_id == "desired_person_merges:01M1"
    assert merge.pm_id == PM1 and merge.changes == {"survivor_pm_id": (None, PM2)}
    assert "#514" in merge.reason


async def test_archive_retraction_is_refused_until_500_builds_it():
    spec = dataclasses.replace(MANIFEST.tables["desired_people"], retraction="archive")
    manifest = dataclasses.replace(MANIFEST, tables={**MANIFEST.tables, "desired_people": spec})
    store = FakeLiveStore(crosswalk=[xw(P1, PM1)], tables={"people": [live(PM1)]})

    with pytest.raises(ApplierError, match="#500"):
        await diff_desired(_state(desired_people=[PERSON_P1]), manifest, store)


async def test_a_create_minted_this_run_is_consistent_not_stale():
    """Inside the execute transaction the crosswalk already resolves the producer id
    to the row just minted; the re-diff must see an anchored noop, never a stale."""
    held = {
        "id": "n1",
        "person_id": PMX,
        "name": "Emily",
        "name_type": "legal",
        "is_canonical": True,
    }
    store = FakeLiveStore(
        crosswalk=[xw(P3, PMX)], tables={"people": [live(PMX)], "person_names": [held]}
    )
    state = _state(
        desired_people=[{"pm_id": None, "producer_id": P3}],
        desired_person_names=[
            {"pm_id": None, "producer_id": P3, "name": "Emily", "name_type": "legal"}
        ],
    )

    diff = await diff_desired(state, MANIFEST, store, minted={("person", P3): PMX})

    assert _kinds(diff) == {"noop": 2}
