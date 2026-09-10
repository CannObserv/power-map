"""The diff, child shapes (#499 step 4).

`any_then_canonical` (names, acronyms): any row of the key type carrying the
value satisfies the claim; absent everywhere, the canonical row of that type is
the one in dispute; no such row, insert — canonical only when the parent has
none at all. `key` (events): match on the key columns among unarchived rows —
none is an insert, one compares the owned column, more than one is a conflict.
Only owned event types are ever seen; an owned type absent from the snapshot
is a report-only retraction.
"""

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import ApplierError, DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

P1, P3 = "01P1", "01P3"
PM1 = "01M1"
O4, O5 = "01O4", "01O5"
MO4, MO5 = "01N4", "01N5"
DISSOLVED, FOUNDED = "EVT_DISSOLVED", "EVT_FOUNDED"

MANIFEST = load_manifest()
LOOKUPS = {("entity_event_types", "slug", "id"): {"dissolved": DISSOLVED, "founded": FOUNDED}}


def xw(producer_id, pm_id, *, kind="person"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
    }


def live(pm_id, **cols):
    return {"id": pm_id, "archived_at": None, **cols}


def name(row_id, person_id, text, *, name_type="legal", canonical=False):
    return {
        "id": row_id,
        "person_id": person_id,
        "name": text,
        "name_type": name_type,
        "is_canonical": canonical,
    }


def acronym(row_id, org_id, text, *, canonical=False):
    return {"id": row_id, "organization_id": org_id, "acronym": text, "is_canonical": canonical}


def event(row_id, org_id, type_id, year, *, archived=None):
    return {
        "id": row_id,
        "entity_id": org_id,
        "entity_type": "organization",
        "event_type_id": type_id,
        "event_year": year,
        "archived_at": archived,
    }


def _state(**tables) -> DesiredState:
    return DesiredState(
        tables={n: list(tables.get(n, [])) for n in MANIFEST.tables}, build_info=None
    )


def _kinds(diff) -> dict[str, int]:
    return {k: v for k, v in diff.counts.items() if v}


def _person_store(*names):
    return FakeLiveStore(
        crosswalk=[xw(P1, PM1)], tables={"people": [live(PM1)], "person_names": list(names)}
    )


def _person_state(text):
    return _state(
        desired_people=[{"pm_id": PM1, "producer_id": P1}],
        desired_person_names=[
            {"pm_id": PM1, "producer_id": P1, "name": text, "name_type": "legal"}
        ],
    )


def _org_store(*rows, table, anchored=((O5, MO5),)):
    return FakeLiveStore(
        crosswalk=[xw(o, m, kind="organization") for o, m in anchored],
        tables={"organizations": [live(m) for _, m in anchored], table: list(rows)},
        lookups=LOOKUPS,
    )


BOTH = ((O4, MO4), (O5, MO5))


# --- any_then_canonical ---------------------------------------------------------


async def test_a_value_present_on_any_row_of_the_type_is_a_noop():
    """The measured org case: PM holds short (canonical) and long (legal) forms."""
    store = _person_store(
        name("n1", PM1, "Cap Budget", canonical=True), name("n2", PM1, "House Committee on CB")
    )

    diff = await diff_desired(_person_state("House Committee on CB"), MANIFEST, store)

    assert _kinds(diff) == {"noop": 2}


async def test_a_value_absent_everywhere_updates_the_canonical_row_of_the_type():
    """The measured person case: curly quotes vs straight — the display row is in dispute."""
    store = _person_store(name("n1", PM1, 'A.L. "Slim" Rasmussen', canonical=True))

    diff = await diff_desired(_person_state("A. L. “Slim” Rasmussen"), MANIFEST, store)

    update = diff.by_kind("update")[0]
    assert update.entry_id == "desired_person_names:01P1|legal"
    assert update.row_id == "n1" and update.pm_id == PM1
    assert update.changes == {"name": ('A.L. "Slim" Rasmussen', "A. L. “Slim” Rasmussen")}


async def test_a_row_of_another_type_never_satisfies_the_claim():
    store = _person_store(name("n1", PM1, "Mike Padden", name_type="preferred", canonical=True))

    diff = await diff_desired(_person_state("Mike Padden"), MANIFEST, store)

    assert _kinds(diff) == {"noop": 1, "insert": 1}


async def test_absent_with_no_canonical_of_the_type_inserts_non_canonical():
    """A canonical `preferred` and a non-canonical `legal` X: desired legal Y is a new row,
    and it does not take the display pointer — the parent already has one."""
    store = _person_store(
        name("n1", PM1, "Mike", name_type="preferred", canonical=True),
        name("n2", PM1, "Michael Padden"),
    )

    diff = await diff_desired(_person_state("Mike Padden"), MANIFEST, store)

    insert = diff.by_kind("insert")[0]
    assert insert.entry_id == "desired_person_names:01P1|legal"
    assert insert.changes["name"] == (None, "Mike Padden")
    assert insert.changes["name_type"] == (None, "legal")
    assert insert.changes["is_canonical"] == (None, False)


async def test_a_parent_with_no_rows_at_all_gets_a_canonical_insert():
    store = _person_store()

    diff = await diff_desired(_person_state("Someone New"), MANIFEST, store)

    assert diff.by_kind("insert")[0].changes["is_canonical"] == (None, True)


async def test_acronyms_have_no_type_column_so_every_row_is_of_the_type():
    """The measured acronym cases: 36 present on another row, 26 absent, 12 with no canonical."""
    store = _org_store(
        acronym("a1", MO4, "EN", canonical=True),
        acronym("a2", MO5, "WM"),
        acronym("a3", MO5, "WAYS"),
        table="organization_acronyms",
        anchored=BOTH,
    )
    state = _state(
        desired_organizations=[
            {"pm_id": MO4, "producer_id": O4},
            {"pm_id": MO5, "producer_id": O5},
        ],
        desired_organization_acronyms=[
            {"pm_id": MO4, "producer_id": O4, "acronym": "ETT"},  # absent → update canonical
            {"pm_id": MO5, "producer_id": O5, "acronym": "WAYS"},  # present on a row → noop
        ],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 3, "update": 1}
    assert diff.by_kind("update")[0].changes == {"acronym": ("EN", "ETT")}


async def test_rows_exist_but_none_canonical_and_absent_inserts_canonical():
    store = _org_store(
        acronym("a1", MO4, "EN"), table="organization_acronyms", anchored=((O4, MO4),)
    )
    state = _state(
        desired_organizations=[{"pm_id": MO4, "producer_id": O4}],
        desired_organization_acronyms=[{"pm_id": MO4, "producer_id": O4, "acronym": "ETT"}],
    )

    diff = await diff_desired(state, MANIFEST, store)

    insert = diff.by_kind("insert")[0]
    assert insert.changes == {"acronym": (None, "ETT"), "is_canonical": (None, True)}


# --- key (events) -----------------------------------------------------------------


def _events_state(*rows):
    return _state(
        desired_organizations=[{"pm_id": MO5, "producer_id": O5}],
        desired_entity_events=[
            {
                "pm_id": MO5,
                "producer_id": O5,
                "entity_type": "organization",
                "event_type": "dissolved",
                "event_year": year,
            }
            for year in rows
        ],
    )


async def test_an_absent_event_is_an_insert_with_its_type_looked_up():
    store = _org_store(table="entity_events")

    diff = await diff_desired(_events_state(2020), MANIFEST, store)

    insert = diff.by_kind("insert")[0]
    assert insert.entry_id == "desired_entity_events:01O5|dissolved"
    assert insert.changes == {
        "event_year": (None, 2020),
        "event_type_id": (None, DISSOLVED),
        "entity_type": (None, "organization"),
    }


async def test_a_matching_event_is_a_noop_and_a_differing_year_an_update():
    store = _org_store(event("e1", MO5, DISSOLVED, 2020), table="entity_events")

    same = await diff_desired(_events_state(2020), MANIFEST, store)
    other = await diff_desired(_events_state(2022), MANIFEST, store)

    assert _kinds(same) == {"noop": 2}
    update = other.by_kind("update")[0]
    assert update.row_id == "e1" and update.changes == {"event_year": (2020, 2022)}


async def test_two_live_events_of_the_type_are_a_conflict():
    store = _org_store(
        event("e1", MO5, DISSOLVED, 2020), event("e2", MO5, DISSOLVED, 2021), table="entity_events"
    )

    diff = await diff_desired(_events_state(2020), MANIFEST, store)

    conflict = diff.by_kind("conflict")[0]
    assert conflict.entry_id == "desired_entity_events:01O5|dissolved"
    assert "2" in conflict.reason


async def test_an_archived_event_is_not_a_match():
    store = _org_store(
        event("e1", MO5, DISSOLVED, 2020, archived="2026-01-01T00:00:00Z"), table="entity_events"
    )

    diff = await diff_desired(_events_state(2020), MANIFEST, store)

    assert _kinds(diff) == {"noop": 1, "insert": 1}


async def test_an_owned_type_absent_from_the_snapshot_is_a_retract_report():
    store = _org_store(event("e1", MO5, DISSOLVED, 2020), table="entity_events")
    state = _state(desired_organizations=[{"pm_id": MO5, "producer_id": O5}])

    diff = await diff_desired(state, MANIFEST, store)

    retract = diff.by_kind("retract")[0]
    assert retract.entry_id == "desired_entity_events:01O5|dissolved"
    assert retract.pm_id == MO5 and retract.row_id == "e1"
    assert "report" in retract.reason


async def test_an_unowned_type_is_invisible():
    """PM's 315 other org events have no producer column; `founded` here is never an entry."""
    store = _org_store(event("e1", MO5, FOUNDED, 1991), table="entity_events")
    state = _state(desired_organizations=[{"pm_id": MO5, "producer_id": O5}])

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"noop": 1}


async def test_an_unknown_lookup_value_is_an_error():
    store = _org_store(table="entity_events")
    state = _state(
        desired_organizations=[{"pm_id": MO5, "producer_id": O5}],
        desired_entity_events=[
            {
                "pm_id": MO5,
                "producer_id": O5,
                "entity_type": "organization",
                "event_type": "exploded",
                "event_year": 2020,
            }
        ],
    )

    with pytest.raises(ApplierError, match="exploded"):
        await diff_desired(state, MANIFEST, store)


# --- child rows of a create ---------------------------------------------------------


async def test_child_rows_of_a_create_are_inserts_pending_the_create():
    store = FakeLiveStore(tables={"people": [], "person_names": []})
    state = _state(
        desired_people=[{"pm_id": None, "producer_id": P3}],
        desired_person_names=[
            {"pm_id": None, "producer_id": P3, "name": "Emily Alvarado", "name_type": "legal"}
        ],
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"create": 1, "insert": 1}
    insert = diff.by_kind("insert")[0]
    assert insert.pm_id is None and "create" in insert.reason
    assert insert.changes["is_canonical"] == (None, True)


async def test_a_child_row_with_no_parent_and_no_create_is_stale():
    store = FakeLiveStore(tables={"people": [], "person_names": []})
    state = _state(
        desired_person_names=[
            {"pm_id": None, "producer_id": P3, "name": "Orphan", "name_type": "legal"}
        ]
    )

    diff = await diff_desired(state, MANIFEST, store)

    assert _kinds(diff) == {"stale": 1}
