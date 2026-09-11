"""The applier's inputs and row scope (#499 step 2).

Row scope is the *live* crosswalk — `producer_crosswalk` rows with resolution
live or merged, read at run time — never the pm_id a desired row carries. A
desired row that disagrees with the live crosswalk is `stale`: the desired
state predates a merge, an archive or a re-seed and must be rebuilt.
"""

import json

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import (  # noqa: E402
    ApplierError,
    DesiredState,
    Scope,
    scope_rows,
)
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from src.core.ingestion.mapping.parquet import write_parquet  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeLiveStore  # noqa: E402

P1, P2, P3, P4 = "01P1", "01P2", "01P3", "01P4"
PM1, PM2, PM9 = "01M1", "01M2", "01M9"


def xw(producer_id, pm_id, resolution, *, kind="person"):
    return {
        "source": PRODUCER_SOURCE,
        "kind": kind,
        "producer_id": producer_id,
        "pm_id": pm_id,
        "resolution": resolution,
    }


SPEC = load_manifest().tables["desired_people"]


async def _scope(*rows):
    return await Scope.load(
        FakeLiveStore(crosswalk=rows), source=PRODUCER_SOURCE, kinds=("person",)
    )


async def test_scope_resolves_only_live_and_merged():
    scope = await _scope(
        xw(P1, PM1, "live"),
        xw(P2, PM2, "merged"),
        xw(P3, "01M3", "archived"),
        xw(P4, None, "missing"),
    )

    assert scope.resolve("person", P1) == PM1
    assert scope.resolve("person", P2) == PM2
    assert scope.resolve("person", P3) is None
    assert scope.resolve("person", P4) is None
    assert scope.in_scope("person") == {P1, P2}


async def test_an_anchored_row_the_live_crosswalk_agrees_with_is_kept():
    scope = await _scope(xw(P1, PM1, "live"))

    kept, stale = scope_rows(SPEC, [{"pm_id": PM1, "producer_id": P1}], scope)

    assert [r["producer_id"] for r in kept] == [P1]
    assert stale == []


async def test_a_row_whose_pm_id_drifted_is_stale():
    """PM merged PM1 into PM9 after the export the desired state was built from."""
    scope = await _scope(xw(P1, PM9, "merged"))

    kept, stale = scope_rows(SPEC, [{"pm_id": PM1, "producer_id": P1}], scope)

    assert kept == []
    assert [e.kind for e in stale] == ["stale"]
    assert stale[0].entry_id == "desired_people:01P1"
    assert stale[0].pm_id == PM1 and stale[0].producer_id == P1
    assert PM9 in stale[0].reason and "drift" in stale[0].reason


async def test_a_row_whose_anchor_left_scope_is_stale():
    scope = await _scope(xw(P1, PM1, "archived"))

    kept, stale = scope_rows(SPEC, [{"pm_id": PM1, "producer_id": P1}], scope)

    assert kept == []
    assert "archived" in stale[0].reason


async def test_a_row_with_no_live_crosswalk_row_is_stale():
    scope = await _scope()

    kept, stale = scope_rows(SPEC, [{"pm_id": PM1, "producer_id": P1}], scope)

    assert kept == []
    assert "no crosswalk row" in stale[0].reason


async def test_a_create_the_live_crosswalk_already_resolves_is_stale():
    """The desired state says create; PM linked the producer id since. Rebuild, never mint."""
    scope = await _scope(xw(P3, "01M3", "live"))

    kept, stale = scope_rows(SPEC, [{"pm_id": None, "producer_id": P3}], scope)

    assert kept == []
    assert "create" in stale[0].reason and "01M3" in stale[0].reason


async def test_a_create_with_no_live_row_is_kept_as_a_create():
    scope = await _scope()

    kept, stale = scope_rows(SPEC, [{"pm_id": None, "producer_id": P3}], scope)

    assert [r["producer_id"] for r in kept] == [P3]
    assert stale == []


async def test_a_merge_table_is_not_scoped_by_producer_id():
    """Merge rows pass through; `_diff_merge` checks the loser's anchor itself (#514)."""
    spec = load_manifest().tables["desired_person_merges"]
    scope = await _scope()
    row = {
        "loser_pm_id": PM1,
        "survivor_pm_id": PM2,
        "loser_producer_id": P1,
        "survivor_producer_id": P2,
    }

    kept, stale = scope_rows(spec, [row], scope)

    assert kept == [row] and stale == []


# --- DesiredState.load --------------------------------------------------------


def test_desired_state_loads_every_manifest_table_as_records(tmp_path):
    from src.core.ingestion.mapping.parquet import TableSpec

    people = TableSpec("desired_people", ("pm_id", "producer_id"), ("TEXT", "TEXT"))
    write_parquet([(PM1, P1), (None, P3)], people, tmp_path / "desired_people.parquet")
    for table in load_manifest().tables:
        if table != "desired_people":
            spec = TableSpec(table, ("pm_id", "producer_id"), ("TEXT", "TEXT"))
            write_parquet([], spec, tmp_path / f"{table}.parquet")
    (tmp_path / "BUILD.json").write_text(json.dumps({"datasets": {"persons": "v1"}}))

    state = DesiredState.load(tmp_path, load_manifest())

    assert state.tables["desired_people"] == [
        {"pm_id": PM1, "producer_id": P1},
        {"pm_id": None, "producer_id": P3},
    ]
    assert state.build_info == {"datasets": {"persons": "v1"}}


def test_a_missing_table_is_an_error_naming_it(tmp_path):
    with pytest.raises(ApplierError, match="desired_people"):
        DesiredState.load(tmp_path, load_manifest())


def test_a_missing_build_info_is_tolerated(tmp_path):
    from src.core.ingestion.mapping.parquet import TableSpec

    for table in load_manifest().tables:
        spec = TableSpec(table, ("pm_id", "producer_id"), ("TEXT", "TEXT"))
        write_parquet([], spec, tmp_path / f"{table}.parquet")

    assert DesiredState.load(tmp_path, load_manifest()).build_info is None


async def test_the_fake_store_carries_id_and_archived_at_whatever_columns_asks_for():
    """CR 9: the engine reads `archived_at` on rows it requested no columns of — an
    entity binding owns no column and still has to see a row archived since the
    export. Both implementations do it; only the signature said otherwise."""
    store = FakeLiveStore(crosswalk=[], tables={"people": [{"id": PM1, "archived_at": None}]})

    rows = await store.entity_rows("people", [PM1], columns=())

    assert set(rows[PM1]) == {"id", "archived_at"}
