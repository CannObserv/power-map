"""The merge shape against live state (#514).

A `merge` row is a producer tombstone: the loser's PM row should fold into the
survivor's. #499 reported every one and read nothing; #514 classifies each
against the live crosswalk and the entity table:

    noop    the crosswalk already resolves the loser's producer id to the survivor
    merge   actionable (effects name the primitive) — both rows live, or PM already
            merged the loser into the survivor and only the anchors lag
    merge   report-only (no effects) — a null survivor, or a table no primitive binds
    stale   anything else: a person decides

and a producer id the tombstone accounts for is never also a `retract`.
"""

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.applier_report import digest_view  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

MANIFEST = load_manifest()
P_LOSER, P_SURVIVOR, P_OTHER = "01PL", "01PS", "01PO"
PM_LOSER, PM_SURVIVOR, PM_ELSEWHERE, PM_OTHER = "01ML", "01MS", "01ME", "01MO"
PREVIEW = {"names": [{"id": "01NM", "action": "move", "into": None}], "assignments": []}


def xw(producer_id, pm_id, resolution="live", kind="person"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": resolution,
    }


def live(pm_id, archived=False):
    return {"id": pm_id, "archived_at": "2026-09-01" if archived else None}


def tomb(entity_id, merged_into):
    return {"entity_type": "person", "entity_id": entity_id, "merged_into": merged_into}


def merge_row(survivor=PM_SURVIVOR, loser=PM_LOSER):
    return {
        "loser_pm_id": loser,
        "survivor_pm_id": survivor,
        "loser_producer_id": P_LOSER,
        "survivor_producer_id": P_SURVIVOR,
    }


def _state(**tables) -> DesiredState:
    return DesiredState(
        tables={name: list(tables.get(name, [])) for name in MANIFEST.tables}, build_info=None
    )


def _store(*, crosswalk=None, people=None, tombstones=(), previews=None):
    return FakeLiveStore(
        crosswalk=crosswalk
        if crosswalk is not None
        else [xw(P_LOSER, PM_LOSER), xw(P_SURVIVOR, PM_SURVIVOR)],
        tables={
            "people": people if people is not None else [live(PM_LOSER), live(PM_SURVIVOR)],
            "deleted_entities": list(tombstones),
        },
        previews=previews or {(PM_LOSER, PM_SURVIVOR): PREVIEW},
    )


async def _merge_entry(store, row=None, table="desired_person_merges"):
    diff = await diff_desired(_state(**{table: [row or merge_row()]}), MANIFEST, store)
    (entry,) = [e for e in diff.entries if e.table == table]
    return entry


# --- classification -----------------------------------------------------------


async def test_both_rows_live_is_an_actionable_merge_carrying_its_preview():
    entry = await _merge_entry(_store())

    assert entry.kind == "merge"
    assert entry.pm_id == PM_LOSER and entry.producer_id == P_LOSER
    assert entry.changes == {"survivor_pm_id": (None, PM_SURVIVOR)}
    assert entry.effects == {"primitive": "person", "preview": PREVIEW}


async def test_a_merge_the_crosswalk_already_resolves_is_a_noop():
    """After the merge re-pointed the loser's anchor — the next night, or the re-diff."""
    store = _store(crosswalk=[xw(P_LOSER, PM_SURVIVOR, "merged"), xw(P_SURVIVOR, PM_SURVIVOR)])

    assert (await _merge_entry(store)).kind == "noop"


async def test_a_null_survivor_stays_report_only():
    entry = await _merge_entry(_store(), merge_row(survivor=None))

    assert entry.kind == "merge" and entry.effects == {}
    assert "out of scope" in entry.reason


async def test_a_table_no_primitive_binds_stays_report_only():
    entry = await _merge_entry(_store(), table="desired_organization_merges")

    assert entry.kind == "merge" and entry.effects == {}
    assert "no merge primitive" in entry.reason


@pytest.mark.parametrize(
    "people",
    [[live(PM_LOSER)], [live(PM_LOSER), live(PM_SURVIVOR, archived=True)]],
    ids=["survivor missing", "survivor archived"],
)
async def test_a_survivor_pm_cannot_write_to_is_stale(people):
    entry = await _merge_entry(_store(people=people))

    assert entry.kind == "stale" and "survivor" in entry.reason


async def test_an_archived_loser_is_stale():
    entry = await _merge_entry(_store(people=[live(PM_LOSER, archived=True), live(PM_SURVIVOR)]))

    assert entry.kind == "stale" and "loser" in entry.reason


async def test_a_loser_pm_already_merged_into_the_survivor_repoints_anchors_only():
    """A curator merged the pair by hand first: the merge is done, the anchor lags."""
    store = _store(people=[live(PM_SURVIVOR)], tombstones=[tomb(PM_LOSER, PM_SURVIVOR)])

    entry = await _merge_entry(store)

    assert entry.kind == "merge"
    assert entry.effects == {"primitive": "person", "already_merged": True}


async def test_a_loser_merged_through_a_chain_to_the_survivor_repoints_anchors_only():
    store = _store(
        people=[live(PM_SURVIVOR)],
        tombstones=[tomb(PM_LOSER, PM_ELSEWHERE), tomb(PM_ELSEWHERE, PM_SURVIVOR)],
    )

    assert (await _merge_entry(store)).effects == {"primitive": "person", "already_merged": True}


@pytest.mark.parametrize(
    "loser_anchor",
    [[xw(P_LOSER, PM_ELSEWHERE)], [xw(P_LOSER, None, "missing")], []],
    ids=["drifted", "left scope", "no anchor"],
)
async def test_a_loser_the_live_crosswalk_no_longer_anchors_is_stale(loser_anchor):
    """The mart's `loser_pm_id` is the build's crosswalk export; the live one rules.

    Folding a row the live anchor does not name could never re-point that anchor,
    so every execute would roll back on a merge still pending — `scope_rows`'
    rule for every other shape: rebuild.
    """
    store = _store(
        crosswalk=[*loser_anchor, xw(P_SURVIVOR, PM_SURVIVOR)],
        people=[live(PM_LOSER), live(PM_SURVIVOR), live(PM_ELSEWHERE)],
    )

    entry = await _merge_entry(store)

    assert entry.kind == "stale" and "anchor" in entry.reason
    assert entry.effects == {}
    assert ("merge_preview", "person", PM_LOSER, PM_SURVIVOR) not in store.requested


@pytest.mark.parametrize(
    "tombstones",
    [[], [tomb(PM_LOSER, PM_ELSEWHERE)], [tomb(PM_LOSER, None)]],
    ids=["no tombstone", "merged elsewhere", "deleted outright"],
)
async def test_a_loser_gone_anywhere_but_into_the_survivor_is_stale(tombstones):
    store = _store(people=[live(PM_SURVIVOR), live(PM_ELSEWHERE)], tombstones=tombstones)

    entry = await _merge_entry(store)

    assert entry.kind == "stale" and "loser" in entry.reason


# --- the loser is accounted for, never retracted --------------------------------


def _retracts(diff):
    return [e.producer_id for e in diff.by_kind("retract")]


async def test_a_tombstoned_loser_is_not_also_a_retract():
    store = _store()
    state = _state(
        desired_people=[{"pm_id": PM_SURVIVOR, "producer_id": P_SURVIVOR}],
        desired_person_merges=[merge_row()],
    )

    assert _retracts(await diff_desired(state, MANIFEST, store)) == []


async def test_an_id_resolving_to_a_row_still_published_is_not_a_retract():
    """After the merge: the mart drops the row, the anchor now names the survivor."""
    store = _store(crosswalk=[xw(P_LOSER, PM_SURVIVOR, "merged"), xw(P_SURVIVOR, PM_SURVIVOR)])
    state = _state(desired_people=[{"pm_id": PM_SURVIVOR, "producer_id": P_SURVIVOR}])

    assert _retracts(await diff_desired(state, MANIFEST, store)) == []


async def test_a_producer_id_that_simply_vanished_is_still_a_retract():
    store = _store(
        crosswalk=[xw(P_SURVIVOR, PM_SURVIVOR), xw(P_OTHER, PM_OTHER)],
        people=[live(PM_SURVIVOR), live(PM_OTHER)],
    )
    state = _state(desired_people=[{"pm_id": PM_SURVIVOR, "producer_id": P_SURVIVOR}])

    assert _retracts(await diff_desired(state, MANIFEST, store)) == [P_OTHER]


# --- effects are what the digest approves -----------------------------------------


async def test_effects_enter_the_digest_view_only_when_present():
    """A digest over entries without effects must not move: the streak rides on it."""
    actionable = await _merge_entry(_store())
    report_only = await _merge_entry(_store(), merge_row(survivor=None))

    assert digest_view(actionable)["effects"] == {"primitive": "person", "preview": PREVIEW}
    assert "effects" not in digest_view(report_only)
