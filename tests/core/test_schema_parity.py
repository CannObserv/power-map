"""Schema-object parity diff logic (#315, #331).

Pure-unit coverage of ``diff_defs`` — the kind-agnostic reference-vs-target
comparison that backs ``scripts/audit_schema_constraint_parity.py``. No DB:
snapshots are plain dicts, so the diff semantics (missing / mismatched /
target-only) are tested in isolation from asyncpg. ``DeployedSchema`` /
``classify_missing`` / ``advance_streaks`` (#632) split a reference-only object
into real drift or a reference ahead of the deployed ``schema.sql``, over SQL
text. Integration tests exercise the live constraint/function/trigger snapshot
queries against the test DB.
"""

import re
from datetime import date

import pytest

from src.core.db import SCHEMA_PATH
from src.core.schema_parity import (
    ConstraintKey,
    DeployedSchema,
    FunctionKey,
    TriggerKey,
    advance_streaks,
    classify_missing,
    diff_defs,
    format_drift_report,
    snapshot_constraints,
    snapshot_functions,
    snapshot_triggers,
    strip_line_comments,
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


def test_format_report_indents_multiline_function_bodies():
    """A drifted function body (multi-line) keeps continuation lines aligned."""
    fk = FunctionKey(signature="touch_parent_on_link_change()")
    ref = {fk: "CREATE OR REPLACE FUNCTION f()\nRETURNS trigger AS $$\nBEGIN v2 END\n$$;"}
    tgt = {fk: "CREATE OR REPLACE FUNCTION f()\nRETURNS trigger AS $$\nBEGIN v1 END\n$$;"}
    drift = diff_defs(kind="function", reference=ref, target=tgt)
    report = format_drift_report(drift, reference="refdb", target="proddb")
    # The kind namespace prefixes the object identity.
    assert "  - function.touch_parent_on_link_change()" in report
    # Continuation lines of the body are indented to align under the label, not
    # spilled to column 0 (would be a bare "RETURNS trigger AS $$" line).
    assert "\nRETURNS trigger" not in report
    assert "                 RETURNS trigger AS $$" in report  # 17-space continuation indent


# --- deployed-schema classification (#632) -----------------------------------

_DEPLOYED_SQL = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organizations(id),
    birth_year INT CHECK (birth_year > 0),
    first_name TEXT,
    last_name TEXT,
    UNIQUE (first_name, last_name),
    CONSTRAINT chk_people_named CHECK (first_name IS NOT NULL)
);
DO $$ BEGIN
    ALTER TABLE people ADD CONSTRAINT people_status_check CHECK (status <> '');
END $$;
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$ BEGIN RETURN NEW; END $$
    LANGUAGE plpgsql;
CREATE FUNCTION public.fn_qualified(a int) RETURNS int AS $$ SELECT a $$ LANGUAGE sql;
-- refuse_delete_while_linked() is planned; only mentioned here, never created.
-- A nickname column is planned too: a word in a comment is not a column.
CREATE FUNCTION "public"."fn_quoted"() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql;
CREATE TABLE "public".widgets (id TEXT PRIMARY KEY);
CREATE OR REPLACE TRIGGER trg_updated_at_people
    BEFORE UPDATE ON people FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DO $$ BEGIN
    CREATE TRIGGER trg_people_search_tsv BEFORE INSERT ON people
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""


@pytest.fixture
def deployed():
    return DeployedSchema.from_sql(_DEPLOYED_SQL)


@pytest.mark.parametrize(
    "signature",
    ["set_updated_at()", "fn_qualified(integer)", "fn_quoted()", "SET_UPDATED_AT()"],
)
def test_deployed_declares_created_functions(deployed, signature):
    """``CREATE [OR REPLACE] FUNCTION [public.]name(`` declares it, any overload,
    quoted or not."""
    assert deployed.declares("function", FunctionKey(signature=signature))


def test_deployed_ignores_a_function_only_mentioned(deployed):
    """A name in a comment or a call is not a declaration: CREATE … FUNCTION only."""
    assert not deployed.declares("function", FunctionKey(signature="refuse_delete_while_linked()"))


@pytest.mark.parametrize("name", ["trg_updated_at_people", "trg_people_search_tsv"])
def test_deployed_declares_created_triggers(deployed, name):
    """Top-level ``CREATE OR REPLACE TRIGGER`` and a guarded ``DO``-block ``CREATE TRIGGER``."""
    assert deployed.declares("trigger", TriggerKey(table="people", name=name))


def test_deployed_declares_a_quoted_schema_qualified_table(deployed):
    """``CREATE TABLE "public".widgets`` creates ``widgets``, not ``public``."""
    assert deployed.declares("constraint", ConstraintKey(table="widgets", name="widgets_pkey"))
    assert "public" not in deployed.tables


def test_deployed_does_not_declare_an_absent_trigger(deployed):
    assert not deployed.declares(
        "trigger", TriggerKey(table="people", name="trg_people_inbound_event_links")
    )


@pytest.mark.parametrize(
    "name",
    [
        "chk_people_named",  # explicitly named inline
        "people_status_check",  # named only by its reconciliation DO block
        "people_pkey",  # implicit: inline PRIMARY KEY
        "people_org_id_fkey",  # implicit: inline REFERENCES
        "people_birth_year_check",  # implicit: inline CHECK
        "people_birth_year_check1",  # implicit, collision-numbered
        "people_first_name_last_name_key",  # implicit: multi-column UNIQUE
        "people_check",  # implicit: table CHECK naming no column
        "people_" + "x" * 56,  # 63 bytes: a truncated implicit name, unparseable
    ],
)
def test_deployed_declares_named_and_implicit_constraints(deployed, name):
    """Most constraints never appear literally in schema.sql (PG generates the name
    for an inline PRIMARY KEY / REFERENCES / CHECK / UNIQUE), so an implicit-shaped
    name on a created table whose column words all appear counts as declared."""
    assert deployed.declares("constraint", ConstraintKey(table="people", name=name))


@pytest.mark.parametrize(
    ("table", "name"),
    [
        ("people", "chk_people_adult"),  # explicit name, absent
        ("people", "people_nickname_check"),  # implicit shape, column only in a comment
        ("aliases", "aliases_pkey"),  # table never created
    ],
)
def test_deployed_does_not_declare_absent_constraints(deployed, table, name):
    assert not deployed.declares("constraint", ConstraintKey(table=table, name=name))


def test_an_empty_deployed_schema_is_empty():
    """No tables means a blank or wrong file — the audit treats it as misconfigured."""
    assert DeployedSchema.from_sql("-- nothing here\n").is_empty
    assert not DeployedSchema.from_sql(_DEPLOYED_SQL).is_empty


def test_a_comment_inside_a_string_literal_keeps_the_rest_of_the_line():
    """``'--execute'`` in a message is not a comment: the words after it on the same
    line survive, since a lost column word would read a declared constraint as ahead."""
    deployed = DeployedSchema.from_sql(
        "CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY);\n"
        "RAISE 'run --execute'; ALTER TABLE t ADD COLUMN zebra TEXT; -- trailing comment"
    )
    assert deployed.declares("constraint", ConstraintKey(table="t", name="t_zebra_check"))
    assert "trailing" not in deployed.words


#: A ``CREATE FUNCTION|TRIGGER|TABLE`` inside a quoted literal: ``'…'`` or a named
#: ``$tag$…$tag$`` (an anonymous ``$$`` is a function or ``DO`` body, read as text).
_DYNAMIC_DDL_RE = re.compile(
    r"('(?:[^']|'')*'|\$\w+\$.*?\$\w+\$)",
    re.DOTALL,
)
_CREATE_DDL_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?(?:UNLOGGED\s+)?"
    r"(?:FUNCTION|TRIGGER|TABLE)\b",
    re.IGNORECASE,
)


def test_schema_sql_creates_no_object_through_dynamic_sql():
    """``DeployedSchema`` reads names from ``CREATE`` text, so a quoted string that an
    ``EXECUTE`` turns into a function, trigger or table would hide its name and read
    a deployed object as ahead. Keep such DDL out of schema.sql, or teach the reader."""
    sql = strip_line_comments(SCHEMA_PATH.read_text())
    literals = [m.group(1) for m in _DYNAMIC_DDL_RE.finditer(sql)]
    assert [lit for lit in literals if _CREATE_DDL_RE.search(lit)] == []


@pytest.mark.parametrize(
    "sql",
    [
        "EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON t', 'trg');",
        "v := 'CREATE OR REPLACE FUNCTION f() RETURNS int'; EXECUTE v;",
        "EXECUTE format($f$CREATE TABLE %I (id int)$f$, 'x');",
    ],
)
def test_dynamic_ddl_detector(sql):
    literals = [m.group(1) for m in _DYNAMIC_DDL_RE.finditer(sql)]
    assert any(_CREATE_DDL_RE.search(lit) for lit in literals)


def test_deployed_rejects_an_unknown_kind(deployed):
    with pytest.raises(ValueError, match="index"):
        deployed.declares("index", ConstraintKey(table="people", name="people_pkey"))


def test_classify_missing_splits_drift_from_reference_ahead(deployed):
    """Missing in target + declared by the deployed schema.sql → drift; missing but
    undeclared → reference ahead (a pending deploy or a stray branch apply)."""
    declared = TriggerKey(table="people", name="trg_updated_at_people")
    pending = TriggerKey(table="people", name="trg_people_inbound_event_links")
    drift = diff_defs(kind="trigger", reference={declared: "a", pending: "b"}, target={})

    classified = classify_missing(drift, deployed)

    assert classified.missing_in_target == [declared]
    assert classified.reference_ahead == [pending]
    assert classified.has_drift
    assert classified.drift_count == 1


def test_reference_ahead_alone_is_not_drift(deployed):
    pending = FunctionKey(signature="refuse_delete_while_linked()")
    drift = classify_missing(
        diff_defs(kind="function", reference={pending: "b"}, target={}), deployed
    )
    assert not drift.has_drift
    assert drift.drift_count == 0
    assert drift.reference_ahead == [pending]


def test_classify_missing_leaves_mismatches_and_target_only_alone(deployed):
    k = FunctionKey(signature="set_updated_at()")
    extra = FunctionKey(signature="prod_only()")
    drift = classify_missing(
        diff_defs(kind="function", reference={k: "v2"}, target={k: "v1", extra: "x"}), deployed
    )
    assert drift.mismatched == [(k, "v2", "v1")]
    assert drift.target_only == [extra]
    assert drift.reference_ahead == []


def test_format_report_names_reference_ahead_as_pending_deploy(deployed):
    pending = FunctionKey(signature="refuse_delete_while_linked()")
    drift = classify_missing(
        diff_defs(kind="function", reference={pending: "b"}, target={}), deployed
    )
    report = format_drift_report(drift, reference="ref", target="prod")
    assert "reference ahead of the deployed schema (pending deploy?)" in report
    assert "  - function.refuse_delete_while_linked()" in report
    assert "MISSING" not in report


_D1, _D2 = date(2026, 10, 9), date(2026, 10, 10)


def test_format_report_says_classified_missing_objects_are_declared(deployed):
    """After classification, MISSING means declared by the deployed schema.sql — the
    header says so, so a mixed report shows why one list fails and the other not."""
    declared = TriggerKey(table="people", name="trg_updated_at_people")
    pending = TriggerKey(table="people", name="trg_people_inbound_event_links")
    raw = diff_defs(kind="trigger", reference={declared: "a", pending: "b"}, target={})
    classified = format_drift_report(classify_missing(raw, deployed), reference="r", target="p")
    unclassified = format_drift_report(raw, reference="r", target="p")
    assert "MISSING in target (p), declared by the deployed schema.sql:" in classified
    assert "declared by the deployed schema.sql" not in unclassified


def test_advance_streaks_counts_consecutive_run_days_per_object():
    """A label still ahead gains a run, a new one starts at 1, a cleared one drops."""
    previous = {
        "function.a()": {"runs": 2, "last_day": "2026-10-09"},
        "trigger.t.gone": {"runs": 5, "last_day": "2026-10-09"},
    }
    assert advance_streaks(previous, ["function.a()", "function.b()"], today=_D2) == {
        "function.a()": {"runs": 3, "last_day": "2026-10-10"},
        "function.b()": {"runs": 1, "last_day": "2026-10-10"},
    }


def test_advance_streaks_counts_one_run_per_utc_day():
    """A manual re-run the same day is not another run: the timer is daily."""
    first = advance_streaks({}, ["function.a()"], today=_D1)
    assert advance_streaks(first, ["function.a()"], today=_D1) == first


def test_advance_streaks_from_nothing():
    assert advance_streaks({}, [], today=_D1) == {}


def test_real_schema_sql_declares_the_2026_10_09_objects():
    """The PR #629 objects that tripped the 2026-10-09 run are in schema.sql now, so
    their absence from prod would be real drift, not a pending deploy."""
    deployed = DeployedSchema.from_sql(SCHEMA_PATH.read_text())
    assert deployed.declares("function", FunctionKey(signature="lock_identifier_entity()"))
    assert deployed.declares("function", FunctionKey(signature="refuse_delete_while_linked()"))
    for table, name in [
        ("identifiers", "trg_identifiers_entity"),
        ("people", "trg_people_inbound_event_links"),
        ("organizations", "trg_organizations_inbound_event_links"),
    ]:
        assert deployed.declares("trigger", TriggerKey(table=table, name=name))


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


@pytest.mark.integration
async def test_schema_sql_declares_every_live_object(db_pool):
    """The matching rules hold on the real schema: of the test DB's constraints
    (most named by Postgres, never written in schema.sql), functions and triggers,
    none reads as ahead of the schema.sql that built it. A failure names either a
    rule gap or a stray branch's ``apply-schema.sh --test`` still on the shared
    test DB."""
    async with db_pool.acquire() as conn:
        snaps = {
            "constraint": await snapshot_constraints(conn),
            "function": await snapshot_functions(conn),
            "trigger": await snapshot_triggers(conn),
        }
    deployed = DeployedSchema.from_sql(SCHEMA_PATH.read_text())
    ahead = sorted(
        f"{kind}.{k.label}"
        for kind, snap in snaps.items()
        for k in snap
        if not deployed.declares(kind, k)
    )
    assert ahead == []
