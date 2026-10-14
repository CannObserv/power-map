"""The diff and writer for org links (#592): a child binding that archives.

`desired_organization_lineage` owns `succeeded_by` / `merged_with` / `split_from`
on anchored pairs, matched on (type, linked org) among unarchived rows; only the
year is owned. A link PM holds that the snapshot dropped is archived, gated as
an archive — but only one whose two ends are both in scope. Links to orgs that
are not, and every other event type, are invisible to it.
"""

import itertools

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import DesiredState, Diff, Entry, diff_desired  # noqa: E402
from src.core.ingestion.applier_report import verdict_for  # noqa: E402
from src.core.ingestion.applier_write import plan_statements  # noqa: E402
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

MANIFEST = load_manifest()
LINEAGE = "desired_organization_lineage"
O4, O5, O6 = "01O4", "01O5", "01O6"
MO4, MO5, MO6 = "01N4", "01N5", "01N6"
MX = "01NX"  # a PM org no usa-wa anchor names
SUCCEEDED, MERGED, SPLIT, DISSOLVED = "EVT_SB", "EVT_MW", "EVT_SF", "EVT_DISSOLVED"
LOOKUPS = {
    ("entity_event_types", "slug", "id"): {
        "succeeded_by": SUCCEEDED,
        "merged_with": MERGED,
        "split_from": SPLIT,
        "dissolved": DISSOLVED,
    }
}
ANCHORED = ((O4, MO4), (O5, MO5), (O6, MO6))


def xw(producer_id, pm_id):
    return {
        "source": PRODUCER_SOURCE,
        "kind": "organization",
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": "live",
    }


def link(row_id, subject, type_id, linked, year=None, *, archived=None, month=None):
    return {
        "id": row_id,
        "entity_id": subject,
        "entity_type": "organization",
        "event_type_id": type_id,
        "linked_entity_type": "organization",
        "linked_entity_id": linked,
        "event_year": year,
        "event_month": month,
        "archived_at": archived,
    }


def claim(subject, slug, linked, year, *, producer=None, linked_producer=None):
    producers = {m: o for o, m in ANCHORED}
    return {
        "pm_id": subject,
        "producer_id": producer or producers[subject],
        "event_type": slug,
        "linked_pm_id": linked,
        "linked_producer_id": linked_producer or producers.get(linked),
        "event_year": year,
    }


def _store(*rows):
    return FakeLiveStore(
        crosswalk=[xw(o, m) for o, m in ANCHORED],
        tables={
            "organizations": [{"id": m, "archived_at": None} for _, m in ANCHORED]
            + [{"id": MX, "archived_at": None}],
            "entity_events": list(rows),
        },
        lookups=LOOKUPS,
    )


def _state(*claims) -> DesiredState:
    tables = {n: [] for n in MANIFEST.tables}
    tables["desired_organizations"] = [{"pm_id": m, "producer_id": o} for o, m in ANCHORED]
    tables[LINEAGE] = list(claims)
    return DesiredState(tables=tables, build_info=None)


def _lineage(diff) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {}
    for e in diff.entries:
        if e.table == LINEAGE:
            out.setdefault(e.kind, []).append(e)
    return out


# --- diff -------------------------------------------------------------------------


async def test_a_published_link_pm_lacks_is_an_insert_naming_both_orgs():
    diff = await diff_desired(_state(claim(MO5, "succeeded_by", MO4, 2021)), MANIFEST, _store())

    (insert,) = _lineage(diff)["insert"]
    assert insert.entry_id == f"{LINEAGE}:{O5}|succeeded_by|{MO4}"
    assert insert.pm_id == MO5
    assert insert.changes == {
        "event_type_id": (None, SUCCEEDED),
        "linked_entity_id": (None, MO4),
        "event_year": (None, 2021),
        "entity_type": (None, "organization"),
        "linked_entity_type": (None, "organization"),
    }


async def test_a_matching_link_is_a_noop_and_its_month_stays_pms():
    store = _store(link("e1", MO5, SUCCEEDED, MO4, 2021, month=3))

    diff = await diff_desired(_state(claim(MO5, "succeeded_by", MO4, 2021)), MANIFEST, store)

    assert set(_lineage(diff)) == {"noop"}


async def test_an_undated_link_takes_the_published_year():
    """Civic Health: PM's hand-curated edge is undated, usa-wa's is 2026."""
    store = _store(link("e1", MO5, SUCCEEDED, MO4))

    diff = await diff_desired(_state(claim(MO5, "succeeded_by", MO4, 2026)), MANIFEST, store)

    (update,) = _lineage(diff)["update"]
    assert update.row_id == "e1" and update.changes == {"event_year": (None, 2026)}


async def test_a_link_to_another_org_does_not_satisfy_the_claim():
    """One predecessor, three successors: each (type, linked) pair is its own link."""
    store = _store(link("e1", MO5, SUCCEEDED, MO6, 2021))

    diff = await diff_desired(
        _state(claim(MO5, "succeeded_by", MO4, 2021), claim(MO5, "succeeded_by", MO6, 2021)),
        MANIFEST,
        store,
    )

    kinds = _lineage(diff)
    assert [e.entry_id for e in kinds["insert"]] == [f"{LINEAGE}:{O5}|succeeded_by|{MO4}"]
    assert [e.row_id for e in kinds["noop"]] == ["e1"]


async def test_a_dropped_link_between_anchored_orgs_is_archived():
    store = _store(link("e1", MO5, MERGED, MO4, 2001))

    diff = await diff_desired(_state(), MANIFEST, store)

    (archive,) = _lineage(diff)["archive"]
    assert archive.entry_id == f"{LINEAGE}:{O5}|merged_with|{MO4}"
    assert archive.pm_id == MO5 and archive.row_id == "e1"
    assert "absent" in archive.reason


async def test_an_archived_link_is_not_archived_again():
    store = _store(link("e1", MO5, MERGED, MO4, 2001, archived="2026-10-01T00:00:00Z"))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert "archive" not in _lineage(diff)


async def test_a_link_to_an_org_no_anchor_names_is_invisible():
    """Not an anchored pair, so not the dataset's: PM's own, whatever its type."""
    store = _store(link("e1", MO5, SUCCEEDED, MX, 2021))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert _lineage(diff) == {}


async def test_a_link_from_an_org_no_anchor_names_is_invisible():
    store = _store(link("e1", MX, SUCCEEDED, MO5, 2021))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert _lineage(diff) == {}


async def test_other_event_types_are_invisible_to_the_lineage_binding():
    store = _store(
        link("e1", MO5, DISSOLVED, None, 2020) | {"linked_entity_type": None},
    )

    diff = await diff_desired(_state(), MANIFEST, store)

    assert _lineage(diff) == {}


async def test_a_link_that_reappears_after_its_archive_is_a_fresh_insert():
    store = _store(link("e1", MO5, SPLIT, MO4, 2015, archived="2026-10-01T00:00:00Z"))

    diff = await diff_desired(_state(claim(MO5, "split_from", MO4, 2015)), MANIFEST, store)

    assert set(_lineage(diff)) == {"insert"}


async def test_a_dropped_link_blocks_execute_at_the_default_threshold():
    store = _store(link("e1", MO5, MERGED, MO4, 2001))

    diff = await diff_desired(_state(), MANIFEST, store)

    assert verdict_for(diff, MANIFEST.thresholds).verdict == "blocked"


async def test_the_dissolved_binding_never_retracts_a_link():
    """Two bindings share entity_events; each sees only its own owned types."""
    store = _store(link("e1", MO5, SUCCEEDED, MO4, 2021))

    diff = await diff_desired(_state(claim(MO5, "succeeded_by", MO4, 2021)), MANIFEST, store)

    assert not [e for e in diff.entries if e.table == "desired_entity_events"]


# --- write ------------------------------------------------------------------------


def _plan(*entries):
    ids = (f"NEW{n}" for n in itertools.count(1))
    return plan_statements(Diff(list(entries)), MANIFEST, source=PRODUCER_SOURCE, ids=ids)


def _entry(kind, **kw):
    return Entry(
        entry_id=f"{LINEAGE}:{O5}|merged_with|{MO4}",
        table=LINEAGE,
        kind=kind,
        producer_id=O5,
        pm_id=MO5,
        **kw,
    )


def test_a_link_archive_archives_the_event_row_and_stamps_no_anchor():
    """The anchor is the org's, and the org stays; only the link goes."""
    statements, _ = _plan(_entry("archive", row_id="e1"))

    assert [(s.kind, s.table, s.sql, s.args) for s in statements] == [
        (
            "archive",
            "entity_events",
            "UPDATE entity_events SET archived_at = NOW() WHERE id = $1 AND archived_at IS NULL",
            ("e1",),
        )
    ]


def test_a_link_insert_names_the_linked_org():
    statements, _ = _plan(
        _entry(
            "insert",
            changes={
                "event_type_id": (None, MERGED),
                "linked_entity_id": (None, MO4),
                "event_year": (None, 2001),
                "entity_type": (None, "organization"),
                "linked_entity_type": (None, "organization"),
            },
        )
    )

    ((sql, args),) = [(s.sql, s.args) for s in statements]
    columns = (
        "id, entity_id, entity_type, event_type_id, event_year,"
        " linked_entity_id, linked_entity_type, visibility"
    )
    assert sql == f"INSERT INTO entity_events ({columns}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
    assert args == ("NEW1", MO5, "organization", MERGED, 2001, MO4, "organization", "public")


async def test_an_owned_type_pm_has_no_vocabulary_for_has_nothing_to_archive():
    """No type id, no live row of the type: skipped, never an unknown-lookup error."""
    store = _store(link("e1", MO5, SUCCEEDED, MO4, 2021))
    store._lookups[("entity_event_types", "slug", "id")] = {
        "succeeded_by": SUCCEEDED,
        "dissolved": DISSOLVED,
    }

    diff = await diff_desired(_state(claim(MO5, "succeeded_by", MO4, 2021)), MANIFEST, store)

    assert set(_lineage(diff)) == {"noop"}
