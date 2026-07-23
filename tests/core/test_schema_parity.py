"""Constraint-parity diff logic (#315).

Pure-unit coverage of ``diff_constraints`` — the reference-vs-target comparison
that backs ``scripts/audit_schema_constraint_parity.py``. No DB: snapshots are
plain dicts, so the diff semantics (missing / mismatched / target-only) are
tested in isolation from asyncpg. One integration test exercises the live
snapshot query against the test DB.
"""

import pytest

from src.core.schema_parity import ConstraintKey, diff_constraints, snapshot_constraints


def _key(table, name):
    return ConstraintKey(table=table, name=name)


def test_no_drift_when_identical():
    snap = {_key("t", "c1"): "CHECK (x > 0)"}
    drift = diff_constraints(reference=snap, target=dict(snap))
    assert not drift.has_drift
    assert drift.missing_in_target == []
    assert drift.mismatched == []
    assert drift.target_only == []


def test_missing_in_target_is_drift():
    ref = {_key("entity_events", "ck_year"): "CHECK (event_year <> 0)"}
    drift = diff_constraints(reference=ref, target={})
    assert drift.has_drift
    assert drift.missing_in_target == [_key("entity_events", "ck_year")]


def test_mismatched_def_is_drift_and_carries_both_sides():
    key = _key("entity_events", "fk_addr")
    ref = {key: "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL"}
    tgt = {key: "FOREIGN KEY (a) REFERENCES addresses(id)"}
    drift = diff_constraints(reference=ref, target=tgt)
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
    # A constraint present in prod but absent from the reference is surfaced for
    # visibility (stale reference, or a prod-only leftover) but does not fail the
    # guard — the guard's contract is "prod must carry everything the reference
    # has", not "prod must carry *only* what the reference has".
    tgt = {_key("t", "leftover"): "CHECK (true)"}
    drift = diff_constraints(reference={}, target=tgt)
    assert not drift.has_drift
    assert drift.target_only == [_key("t", "leftover")]


def test_drift_keys_are_sorted_for_stable_reporting():
    ref = {
        _key("b_table", "c"): "CHECK (1)",
        _key("a_table", "z"): "CHECK (2)",
        _key("a_table", "a"): "CHECK (3)",
    }
    drift = diff_constraints(reference=ref, target={})
    assert drift.missing_in_target == [
        _key("a_table", "a"),
        _key("a_table", "z"),
        _key("b_table", "c"),
    ]


pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
async def test_snapshot_constraints_against_live_db(db_pool):
    async with db_pool.acquire() as conn:
        snap = await snapshot_constraints(conn)
    # Sanity: the FK we repaired in #315 is present with its SET NULL action.
    fk = ConstraintKey(table="entity_events", name="entity_events_event_place_address_id_fkey")
    assert fk in snap
    assert "ON DELETE SET NULL" in snap[fk]
    # A freshly-applied DB diffed against itself has zero drift.
    drift = diff_constraints(reference=snap, target=dict(snap))
    assert not drift.has_drift
