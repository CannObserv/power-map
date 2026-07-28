"""Schema-object parity diff logic (#315, #331).

Pure-unit coverage of ``diff_defs`` — the kind-agnostic reference-vs-target
comparison that backs ``scripts/audit_schema_constraint_parity.py``. No DB:
snapshots are plain dicts, so the diff semantics (missing / mismatched /
target-only) are tested in isolation from asyncpg. Integration tests exercise
the live constraint/function/trigger snapshot queries against the test DB.
"""

import pytest

from src.core.schema_parity import (
    ConstraintKey,
    FunctionKey,
    TriggerKey,
    diff_defs,
    snapshot_constraints,
    snapshot_functions,
    snapshot_triggers,
)


def _key(table, name):
    return ConstraintKey(table=table, name=name)


def test_no_drift_when_identical():
    snap = {_key("t", "c1"): "CHECK (x > 0)"}
    drift = diff_defs(kind="constraint", reference=snap, target=dict(snap))
    assert not drift.has_drift
    assert drift.missing_in_target == []
    assert drift.mismatched == []
    assert drift.target_only == []


def test_missing_in_target_is_drift():
    ref = {_key("entity_events", "ck_year"): "CHECK (event_year <> 0)"}
    drift = diff_defs(kind="constraint", reference=ref, target={})
    assert drift.has_drift
    assert drift.missing_in_target == [_key("entity_events", "ck_year")]
    assert drift.drift_count == 1


def test_mismatched_def_is_drift_and_carries_both_sides():
    key = _key("entity_events", "fk_addr")
    ref = {key: "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL"}
    tgt = {key: "FOREIGN KEY (a) REFERENCES addresses(id)"}
    drift = diff_defs(kind="constraint", reference=ref, target=tgt)
    assert drift.has_drift
    assert drift.mismatched == [
        (
            key,
            "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL",
            "FOREIGN KEY (a) REFERENCES addresses(id)",
        )
    ]
    assert drift.missing_in_target == []


def test_target_only_is_reported_but_not_drift():
    # An object present in prod but absent from the reference is surfaced for
    # visibility (stale reference, or a prod-only leftover/hotfix) but does not
    # fail the guard — the guard's contract is "prod must carry everything the
    # reference has", not "prod must carry *only* what the reference has".
    tgt = {_key("t", "leftover"): "CHECK (true)"}
    drift = diff_defs(kind="constraint", reference={}, target=tgt)
    assert not drift.has_drift
    assert drift.target_only == [_key("t", "leftover")]


def test_drift_keys_are_sorted_for_stable_reporting():
    ref = {
        _key("b_table", "c"): "CHECK (1)",
        _key("a_table", "z"): "CHECK (2)",
        _key("a_table", "a"): "CHECK (3)",
    }
    drift = diff_defs(kind="constraint", reference=ref, target={})
    assert drift.missing_in_target == [
        _key("a_table", "a"),
        _key("a_table", "z"),
        _key("b_table", "c"),
    ]


def test_diff_defs_is_kind_agnostic_over_functions():
    fk = FunctionKey(signature="touch_parent_on_link_change()")
    ref = {fk: "CREATE OR REPLACE FUNCTION ... v2 ... "}
    tgt = {fk: "CREATE OR REPLACE FUNCTION ... v1 ... "}
    drift = diff_defs(kind="function", reference=ref, target=tgt)
    assert drift.kind == "function"
    assert drift.has_drift
    assert drift.mismatched[0][0] is fk


def test_diff_defs_is_kind_agnostic_over_triggers():
    tk = TriggerKey(table="links", name="trg_touch_entity_on_link_change")
    ref = {tk: "CREATE TRIGGER ... a"}
    drift = diff_defs(kind="trigger", reference=ref, target={})
    assert drift.kind == "trigger"
    assert drift.missing_in_target == [tk]


# --- live snapshot integration ------------------------------------------------


@pytest.mark.integration
async def test_snapshot_constraints_against_live_db(db_pool):
    async with db_pool.acquire() as conn:
        snap = await snapshot_constraints(conn)
    # Sanity: the FK we repaired in #315 is present with its SET NULL action.
    fk = ConstraintKey(table="entity_events", name="entity_events_event_place_address_id_fkey")
    assert fk in snap
    assert "ON DELETE SET NULL" in snap[fk]
    # A freshly-applied DB diffed against itself has zero drift.
    drift = diff_defs(kind="constraint", reference=snap, target=dict(snap))
    assert not drift.has_drift


@pytest.mark.integration
async def test_snapshot_functions_captures_change_feed_functions(db_pool):
    async with db_pool.acquire() as conn:
        snap = await snapshot_functions(conn)
    sigs = {k.signature for k in snap}
    # #327 change-feed touch functions are ours to guard and must be present.
    assert "touch_parent_on_link_change()" in sigs
    assert "touch_parent_on_contact_change()" in sigs
    assert "fn_record_entity_change()" in sigs
    # Every captured def is a real function body (guards the pg_get_functiondef col).
    assert all(d.startswith("CREATE OR REPLACE FUNCTION") for d in snap.values())
    # Self-diff is clean.
    assert not diff_defs(kind="function", reference=snap, target=dict(snap)).has_drift


@pytest.mark.integration
async def test_snapshot_functions_excludes_extension_functions(db_pool):
    """pg_trgm / unaccent / vector install functions into public — not ours to guard."""
    async with db_pool.acquire() as conn:
        snap = await snapshot_functions(conn)
    names = {k.signature.split("(", 1)[0] for k in snap}
    # `similarity` (pg_trgm), `unaccent` (unaccent) live in public but are
    # extension-owned; the pg_depend anti-join must exclude them.
    assert "similarity" not in names
    assert "unaccent" not in names


@pytest.mark.integration
async def test_snapshot_triggers_captures_touch_triggers_and_excludes_internal(db_pool):
    async with db_pool.acquire() as conn:
        snap = await snapshot_triggers(conn)
    # #327 change-feed touch trigger present, keyed on (table, name).
    link_trg = TriggerKey(table="links", name="trg_touch_entity_on_link_change")
    assert link_trg in snap
    assert "TRIGGER" in snap[link_trg]
    # No internal FK/constraint-enforcement triggers leak in (they start with RI_).
    assert not any(k.name.startswith("RI_ConstraintTrigger") for k in snap)
    assert not diff_defs(kind="trigger", reference=snap, target=dict(snap)).has_drift
