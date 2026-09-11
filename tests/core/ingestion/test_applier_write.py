"""The writer and the transaction (#499 step 6).

Statements are planned from the diff in a fixed order — entity creates (each
with its crosswalk row), then child rows, then columns — so a created parent
exists before a child names it. Every statement names exactly the columns the
entry changed plus what an insert needs; the two acceptance proofs are asserted
here on the emitted SQL. `apply_diff` runs them in one transaction and re-diffs
inside it: anything still to write means rollback.
"""

import itertools

import pytest

pytest.importorskip("duckdb")

from src.core.ingestion.applier import ApplierError, Diff, Entry  # noqa: E402
from src.core.ingestion.applier_write import (  # noqa: E402
    VerificationFailed,
    apply_diff,
    plan_statements,
)
from src.core.ingestion.crosswalk import PRODUCER_SOURCE  # noqa: E402
from src.core.ingestion.mapping import load_manifest  # noqa: E402
from tests.core.ingestion.applier_fakes import FakeConn  # noqa: E402

MANIFEST = load_manifest()
P1, P3 = "01P1", "01P3"
PM1 = "01M1"
O2, O11 = "01O2", "01O11"
MO2 = "01N2"
PARENT_SQL = "UPDATE organizations SET parent_id = $1 WHERE id = $2"


def E(kind, table, key, **kw) -> Entry:
    return Entry(
        entry_id=f"{table}:{key}",
        table=table,
        kind=kind,
        producer_id=kw.pop("producer_id", key.split("|")[0]),
        pm_id=kw.pop("pm_id", None),
        **kw,
    )


def parent_update(new=2, *, pm_id=MO2, producer=O2):
    return E(
        "update",
        "desired_organization_parents",
        producer,
        pm_id=pm_id,
        changes={"parent_id": (1, new)},
    )


def name_changes(text, *, canonical):
    return {"name": (None, text), "name_type": (None, "legal"), "is_canonical": (None, canonical)}


def _ids():
    return (f"NEW{n}" for n in itertools.count(1))


def _plan(*entries):
    return plan_statements(Diff(list(entries)), MANIFEST, source=PRODUCER_SOURCE, ids=_ids())


def _sql(statements, kind=None):
    return [(s.sql, s.args) for s in statements if kind is None or s.kind == kind]


# --- column scoping -------------------------------------------------------------


def test_an_update_names_exactly_the_changed_owned_columns():
    """Acceptance: a column outside the asserted set is never written."""
    entry = E(
        "update",
        "desired_person_names",
        "01P1|legal",
        pm_id=PM1,
        row_id="n1",
        changes={"name": ("old", "new")},
    )

    statements, _ = _plan(entry)

    assert _sql(statements) == [("UPDATE person_names SET name = $1 WHERE id = $2", ("new", "n1"))]
    assert not any("notes" in s.sql or "visibility" in s.sql for s in statements)


def test_a_column_update_targets_the_entity_row():
    statements, _ = _plan(parent_update("y"))

    assert _sql(statements) == [(PARENT_SQL, ("y", MO2))]


# --- row scoping ----------------------------------------------------------------


def test_entries_that_are_not_writes_yield_no_statement():
    """Acceptance: a row outside the crosswalk is never touched — it has no write entry;
    retracts, merges and conflicts are reports and produce nothing either."""
    statements, minted = _plan(
        E("noop", "desired_people", P1, pm_id=PM1),
        E("retract", "desired_people", "01P2", pm_id="01M2", reason="report"),
        E("merge", "desired_person_merges", PM1, pm_id=PM1),
        E("conflict", "desired_entity_events", "01O5|dissolved", pm_id="01N5"),
    )

    assert statements == [] and minted == {}


def test_a_stale_entry_refuses_to_plan_anything():
    stale = E("stale", "desired_people", P1, pm_id=PM1, reason="drift")

    with pytest.raises(ApplierError, match="stale"):
        _plan(stale, parent_update())


# --- creates and order ------------------------------------------------------------


def test_creates_come_first_mint_ids_and_their_crosswalk_rows():
    child = E(
        "insert",
        "desired_person_names",
        "01P3|legal",
        changes=name_changes("Emily", canonical=True),
        reason="on a row this run creates",
    )
    column = E("update", "desired_organization_parents", O11, changes={"parent_id": (None, MO2)})

    statements, minted = _plan(
        child, column, E("create", "desired_people", P3), E("create", "desired_organizations", O11)
    )

    assert minted == {("person", P3): "NEW1", ("organization", O11): "NEW3"}
    assert [s.kind for s in statements] == [
        "entity",
        "crosswalk",
        "entity",
        "crosswalk",
        "insert",
        "column",
    ]
    assert _sql(statements, "entity")[0] == ("INSERT INTO people (id) VALUES ($1)", ("NEW1",))
    crosswalk_sql, crosswalk_args = _sql(statements, "crosswalk")[0]
    assert crosswalk_sql.startswith("INSERT INTO producer_crosswalk (")
    assert crosswalk_args == ("NEW2", PRODUCER_SOURCE, "person", P3, "NEW1", "NEW1", "live")
    assert "NEW1" in _sql(statements, "insert")[0][1]  # the child names the minted parent
    assert _sql(statements, "column")[0] == (PARENT_SQL, (MO2, "NEW3"))


def test_a_child_insert_carries_the_parent_the_changes_and_the_defaults():
    entry = E(
        "insert",
        "desired_person_names",
        "01P1|legal",
        pm_id=PM1,
        changes=name_changes("Mike Padden", canonical=False),
    )

    statements, _ = _plan(entry)

    sql, args = _sql(statements)[0]
    columns = "id, person_id, is_canonical, name, name_type, visibility"
    assert sql == f"INSERT INTO person_names ({columns}) VALUES ($1, $2, $3, $4, $5, $6)"
    assert args == ("NEW1", PM1, False, "Mike Padden", "legal", "public")


def test_an_event_insert_uses_the_looked_up_type_and_the_constants():
    entry = E(
        "insert",
        "desired_entity_events",
        "01O5|dissolved",
        pm_id="01N5",
        changes={
            "event_year": (None, 2020),
            "event_type_id": (None, "EVT_DISSOLVED"),
            "entity_type": (None, "organization"),
        },
    )

    statements, _ = _plan(entry)

    sql, args = _sql(statements)[0]
    columns = "id, entity_id, entity_type, event_type_id, event_year, visibility"
    assert sql == f"INSERT INTO entity_events ({columns}) VALUES ($1, $2, $3, $4, $5, $6)"
    assert args == ("NEW1", "01N5", "organization", "EVT_DISSOLVED", 2020, "public")


def test_a_child_of_a_create_with_no_create_entry_is_an_error():
    orphan = E(
        "insert", "desired_person_names", "01P3|legal", changes=name_changes("X", canonical=True)
    )

    with pytest.raises(ApplierError, match="01P3"):
        _plan(orphan)


# --- the transaction ---------------------------------------------------------------


async def _apply(diff, conn, rediff):
    return await apply_diff(diff, MANIFEST, conn, source=PRODUCER_SOURCE, rediff=rediff, ids=_ids())


async def test_apply_commits_when_the_rediff_has_nothing_left_to_write():
    conn = FakeConn()

    async def rediff(minted):
        return Diff([E("noop", "desired_organization_parents", O2, pm_id=MO2)])

    result = await _apply(Diff([parent_update()]), conn, rediff)

    assert conn.events == ["begin", "commit"]
    assert [s for s, _ in conn.statements] == [PARENT_SQL]
    assert result.written == 1 and result.minted == {}


async def test_apply_rolls_back_when_the_rediff_still_wants_to_write():
    conn = FakeConn()

    async def rediff(minted):
        return Diff([parent_update(3)])

    with pytest.raises(VerificationFailed, match="desired_organization_parents:01O2"):
        await _apply(Diff([parent_update()]), conn, rediff)

    assert conn.events == ["begin", "rollback"]


async def test_apply_rolls_back_when_the_rediff_found_a_conflict():
    """CR 8: the verification counted only writes and stale, so a conflict the run's own
    inserts created — two live rows now matching a keyed child — committed, and the
    verdict that would have named it was computed before the write and never again."""
    conn = FakeConn()

    async def rediff(minted):
        return Diff(
            [E("conflict", "desired_entity_events", "01O5|dissolved", reason="2 live rows match")]
        )

    with pytest.raises(VerificationFailed, match="conflict"):
        await _apply(Diff([parent_update()]), conn, rediff)

    assert conn.events == ["begin", "rollback"]


async def test_apply_rolls_back_when_the_rediff_is_stale():
    conn = FakeConn()

    async def rediff(minted):
        return Diff([E("stale", "desired_people", P1, pm_id=PM1, reason="drift")])

    with pytest.raises(VerificationFailed, match="stale"):
        await _apply(Diff([parent_update()]), conn, rediff)

    assert conn.events == ["begin", "rollback"]
