"""The diff, the roles binding: identity lookups and the two indexes (#529 steps 2–3).

A role's type and district reach PM as vocabulary slugs, so a create resolves
them through the tables the manifest names; a slug PM does not carry is `stale`,
never a failed INSERT (#302's shape, where `role_types` has no remote write
path). Identity then splits in two (#261): a role with a jurisdiction is
identified structurally, one without it by `lower(title)`, each index partial on
the other's absence — so a row under one index never blocks a row under the
other.
"""

from datetime import UTC, datetime

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, diff_desired  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from tests.core.ingestion.applier_fakes import (  # noqa: E402
    FakeLiveStore,
    manifest_with_roles,
)

MANIFEST = manifest_with_roles()
TABLE = "desired_roles"
ORG, PM_ORG = "01WORG", "01MORG"
SEAT, COMMITTEE = "seat:senate:ld-34", "committee-role:31640"
PM_SEAT, PM_LIVE = "01MSEAT", "01MLIVE"
ARCHIVED = datetime(2026, 9, 1, tzinfo=UTC)
LOOKUPS = {
    ("role_types", "slug", "id"): {"state_senator": "RT_SEN", "committee_member": "RT_CM"},
    ("jurisdictions", "slug", "id"): {"usa-wa-ld-34": "J34"},
}


def xw(kind, producer_id, pm_id, **kw):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
        **kw,
    }


ANCHORS = [xw("organization", ORG, PM_ORG)]


def desired(
    producer_id,
    *,
    pm_id=None,
    role_type="committee_member",
    district=None,
    qualifier=None,
    title="Member",
):
    return {
        "pm_id": pm_id,
        "producer_id": producer_id,
        "org_producer_id": ORG,
        "role_type": role_type,
        "jurisdiction_slug": f"usa-wa-ld-{district}" if district else None,
        "qualifier": qualifier,
        "title": title,
    }


def live(pm_id, *, role_type="RT_CM", jurisdiction=None, qualifier=None, title="Member"):
    return {
        "id": pm_id,
        "archived_at": None,
        "organization_id": PM_ORG,
        "role_type_id": role_type,
        "jurisdiction_id": jurisdiction,
        "qualifier": qualifier,
        "title": title,
    }


def _store(*roles, crosswalk=()):
    return FakeLiveStore(
        crosswalk=[*ANCHORS, *crosswalk], tables={"roles": list(roles)}, lookups=LOOKUPS
    )


async def _entries(store, *rows):
    tables = {name: [] for name in MANIFEST.tables}
    tables[TABLE] = list(rows)
    diff = await diff_desired(DesiredState(tables=tables, build_info=None), MANIFEST, store)
    return {e.producer_id: e for e in diff.entries if e.table == TABLE}


async def _all_entries(store, rows, *, titles=()):
    """Every binding's entries, keyed by (binding, producer id) — the title move is
    diffed in the column pass, so a roles-only view cannot see it."""
    tables = {name: [] for name in MANIFEST.tables}
    tables[TABLE], tables["desired_role_titles"] = list(rows), list(titles)
    diff = await diff_desired(DesiredState(tables=tables, build_info=None), MANIFEST, store)
    return {(e.table, e.producer_id): e for e in diff.entries}


# --- identity lookups (step 2) ----------------------------------------------------


async def test_a_create_writes_the_ids_its_slugs_name():
    entry = (await _entries(_store(), desired(SEAT, role_type="state_senator", district=34)))[SEAT]

    assert entry.kind == "create"
    assert entry.changes == {
        "organization_id": (None, PM_ORG),
        "role_type_id": (None, "RT_SEN"),
        "jurisdiction_id": (None, "J34"),
        "qualifier": (None, None),
        "title": (None, "Member"),
    }


async def test_a_slug_pm_does_not_carry_is_stale():
    """#302: `role_types` has no remote write path, so a new type waits for a deploy."""
    entry = (await _entries(_store(), desired(SEAT, role_type="at_large_senator")))[SEAT]

    assert entry.kind == "stale"
    assert "at_large_senator" in entry.reason and "role_types" in entry.reason


async def test_a_role_without_a_district_writes_no_jurisdiction():
    """A committee seat has none — that is a value, not a missing lookup."""
    entry = (await _entries(_store(), desired(COMMITTEE)))[COMMITTEE]

    assert entry.kind == "create"
    assert entry.changes["jurisdiction_id"] == (None, None)


# --- the two indexes (step 3) -----------------------------------------------------


async def test_a_districted_create_checks_the_structural_index():
    store = _store(live(PM_LIVE, role_type="RT_SEN", jurisdiction="J34"))

    entry = (await _entries(store, desired(SEAT, role_type="state_senator", district=34)))[SEAT]

    assert entry.kind == "conflict"
    assert PM_LIVE in entry.reason and "jurisdiction_id" in entry.reason


async def test_a_role_without_a_district_checks_its_title_index_case_insensitively():
    """uq_role_org_title indexes lower(title): one committee membership per org."""
    store = _store(live(PM_LIVE, title="member"))

    entry = (await _entries(store, desired(COMMITTEE, title="Member")))[COMMITTEE]

    assert entry.kind == "conflict"
    assert PM_LIVE in entry.reason


async def test_a_live_row_under_the_other_index_does_not_block():
    """Each index is partial on the other's absence, so the districted row is invisible
    to the title index — PM holds both today."""
    store = _store(live(PM_SEAT, role_type="RT_SEN", jurisdiction="J34", title="Member"))

    entry = (await _entries(store, desired(COMMITTEE, title="Member")))[COMMITTEE]

    assert entry.kind == "create"


async def test_two_creates_on_one_index_are_rivals():
    rows = [
        desired(SEAT, role_type="state_senator", district=34),
        desired("seat:senate:ld-34:twin", role_type="state_senator", district=34),
    ]

    entries = await _entries(_store(), *rows)

    assert [e.kind for e in entries.values()] == ["conflict", "conflict"]


async def test_two_creates_whose_titles_differ_only_in_case_are_rivals():
    """The index folds, so PM holds one of them — and the second INSERT would raise
    mid-transaction, which is the collision the slot accounting exists to refuse."""
    rows = [desired(COMMITTEE, title="Member"), desired("committee-role:31641", title="member")]

    entries = await _entries(_store(), *rows)

    assert [e.kind for e in entries.values()] == ["conflict", "conflict"]


async def test_a_create_and_a_title_move_onto_one_folded_slot_collide():
    """The move is planned in the column pass, after the create — so it is the move
    that must see the create already holding the slot it folds onto."""
    store = _world(roles=[live(PM_LIVE, title="Member")])
    rows = [
        desired(COMMITTEE, pm_id=PM_LIVE, title="Chair"),
        desired("committee-role:31641", title="chair"),
    ]
    titles = [{"pm_id": PM_LIVE, "producer_id": COMMITTEE, "title": "Chair"}]

    entries = await _all_entries(store, rows, titles=titles)

    assert entries[("desired_roles", "committee-role:31641")].kind == "create"
    moved = entries[("desired_role_titles", COMMITTEE)]
    assert moved.kind == "conflict"
    assert "committee-role:31641" in moved.reason


# --- the dependents guard (step 4) ------------------------------------------------


def assignment(pm_id, *, role=PM_LIVE, archived=False):
    return {
        "id": pm_id,
        "archived_at": ARCHIVED if archived else None,
        "person_id": "01MPERSON",
        "role_id": role,
        "start_date": None,
    }


def _world(*, spans=(), roles=(), assignments=()):
    return FakeLiveStore(
        crosswalk=[*ANCHORS, xw("role", COMMITTEE, PM_LIVE), *spans],
        tables={"roles": list(roles), "role_assignments": list(assignments)},
        lookups=LOOKUPS,
    )


async def test_a_role_the_snapshot_drops_archives_when_its_assignments_go_with_it():
    """The ordinary re-key: usa-wa drops the role's spans with it, so none is stranded."""
    store = _world(
        spans=[xw("assignment", "span-1", "01MRA")],
        roles=[live(PM_LIVE)],
        assignments=[assignment("01MRA")],
    )

    entries = await _entries(store)

    assert entries[COMMITTEE].kind == "archive"


async def test_a_role_whose_live_assignment_this_run_keeps_is_a_conflict():
    """A curated assignment — outside the producer's scope — would be left on an
    archived role, so a person decides instead."""
    store = _world(roles=[live(PM_LIVE)], assignments=[assignment("01MCURATED")])

    entry = (await _entries(store))[COMMITTEE]

    assert entry.kind == "conflict"
    assert "01MCURATED" in entry.reason and "role_assignments" in entry.reason


async def test_an_archived_assignment_never_blocks_the_archive():
    store = _world(roles=[live(PM_LIVE)], assignments=[assignment("01MGONE", archived=True)])

    assert (await _entries(store))[COMMITTEE].kind == "archive"


async def test_a_create_that_wanted_the_blocked_archives_slot_conflicts_too():
    """The role keeps its slot, so the create that counted on it never collides."""
    store = _world(roles=[live(PM_LIVE)], assignments=[assignment("01MCURATED")])

    entries = await _entries(store, desired("committee-role:31641", title="Member"))

    assert entries[COMMITTEE].kind == "conflict"
    assert entries["committee-role:31641"].kind == "conflict"
