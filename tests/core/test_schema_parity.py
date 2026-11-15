"""Schema-object parity diff logic (#315, #331).

Pure-unit coverage of ``diff_defs`` — the kind-agnostic reference-vs-target
comparison that backs ``scripts/audit_schema_constraint_parity.py``. No DB:
snapshots are plain dicts, so the diff semantics (missing / mismatched /
target-only) are tested in isolation from asyncpg. ``DeployedSchema`` /
``classify_missing`` / ``advance_streaks`` (#632) split a reference-only object
into real drift or a reference ahead of the deployed ``schema.sql``, over SQL
text. Integration tests exercise the live constraint/function/trigger/unique-index
snapshot queries against the test DB. Unique indexes (#816) carry their validity
in their def, so an index a failed ``CONCURRENTLY`` build left invalid differs.
"""

import re
from datetime import date

import pytest

from src.core.db import SCHEMA_PATH
from src.core.schema_parity import (
    INVALID_INDEX_MARKER,
    VERSION_SENSITIVE_KINDS,
    ConstraintKey,
    DeployedSchema,
    FunctionKey,
    IndexKey,
    TriggerKey,
    advance_streaks,
    classify_missing,
    diff_defs,
    format_drift_report,
    snapshot_constraints,
    snapshot_functions,
    snapshot_triggers,
    snapshot_unique_indexes,
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_people_full_name ON people (first_name, last_name);
CREATE UNIQUE INDEX CONCURRENTLY uq_people_concurrent ON people (id);
DO $$ BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS uq_people_guarded ON people (birth_year);
EXCEPTION WHEN unique_violation THEN
    RAISE WARNING 'uq_people_guarded not created';
END $$;
ALTER INDEX IF EXISTS uq_people_old RENAME TO uq_people_renamed;
DROP INDEX IF EXISTS uq_people_dropped;
-- uq_people_planned is only mentioned in a comment.
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
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?(?:UNLOGGED\s+)?(?:UNIQUE\s+)?"
    r"(?:FUNCTION|TRIGGER|TABLE|INDEX)\b",
    re.IGNORECASE,
)


def test_schema_sql_creates_no_object_through_dynamic_sql():
    """``DeployedSchema`` reads names from ``CREATE`` text, so a quoted string that an
    ``EXECUTE`` turns into a function, trigger, table or index would hide its name and read
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
        "EXECUTE format('CREATE UNIQUE INDEX %I ON t (a)', 'uq');",
    ],
)
def test_dynamic_ddl_detector(sql):
    literals = [m.group(1) for m in _DYNAMIC_DDL_RE.finditer(sql)]
    assert any(_CREATE_DDL_RE.search(lit) for lit in literals)


#: ``CREATE [UNIQUE] INDEX [CONCURRENTLY] [IF NOT EXISTS] ON`` — no name, so Postgres
#: picks one (``<table>_<cols>_idx``) that the text reader cannot see.
_UNNAMED_INDEX_RE = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?ON\b",
    re.IGNORECASE,
)


def test_schema_sql_names_every_index_it_creates():
    """An unnamed ``CREATE INDEX ON t`` gets a Postgres-chosen name that
    ``DeployedSchema`` never sees, so its absence from prod would read as a pending
    deploy (#816). Name every index."""
    sql = strip_line_comments(SCHEMA_PATH.read_text())
    assert _UNNAMED_INDEX_RE.findall(sql) == []


def test_unnamed_index_detector():
    assert _UNNAMED_INDEX_RE.search("CREATE UNIQUE INDEX CONCURRENTLY ON t (a);")
    assert not _UNNAMED_INDEX_RE.search("CREATE UNIQUE INDEX uq_on ON t (a);")


def test_deployed_rejects_an_unknown_kind(deployed):
    with pytest.raises(ValueError, match="sequence"):
        deployed.declares("sequence", ConstraintKey(table="people", name="people_pkey"))


@pytest.mark.parametrize(
    "name",
    [
        "uq_people_full_name",  # top level, IF NOT EXISTS
        "uq_people_concurrent",  # CONCURRENTLY
        "uq_people_guarded",  # inside a warn-don't-abort DO block (#398/#748)
        "uq_people_renamed",  # the target of ALTER INDEX … RENAME TO
        "UQ_PEOPLE_FULL_NAME",
    ],
)
def test_deployed_declares_created_unique_indexes(deployed, name):
    """``CREATE [UNIQUE] INDEX [CONCURRENTLY] [IF NOT EXISTS] name``, top level or in a
    ``DO`` block, declares it; so does a rename onto the name (#816)."""
    assert deployed.declares("unique_index", IndexKey(table="people", name=name))


@pytest.mark.parametrize("name", ["uq_people_dropped", "uq_people_planned", "uq_people_old"])
def test_deployed_does_not_declare_a_dropped_mentioned_or_renamed_away_index(deployed, name):
    """A ``DROP INDEX``, a comment, or the old side of a rename does not create it."""
    assert not deployed.declares("unique_index", IndexKey(table="people", name=name))


def test_unique_indexes_are_version_sensitive():
    """``pg_get_indexdef`` is server-formatted like function/trigger defs, so a PG
    major mismatch skips the index diff (#816 operator decision)."""
    assert "unique_index" in VERSION_SENSITIVE_KINDS
    assert "constraint" not in VERSION_SENSITIVE_KINDS


def test_index_key_label_names_table_and_index():
    assert IndexKey(table="roles", name="uq_role_org_title").label == "roles.uq_role_org_title"


def test_format_report_namespaces_unique_indexes(deployed):
    declared = IndexKey(table="people", name="uq_people_full_name")
    pending = IndexKey(table="people", name="uq_people_planned")
    drift = classify_missing(
        diff_defs(kind="unique_index", reference={declared: "a", pending: "b"}, target={}),
        deployed,
    )
    report = format_drift_report(drift, reference="r", target="p")
    assert "  - unique_index.people.uq_people_full_name" in report
    assert "  - unique_index.people.uq_people_planned" in report
    assert drift.missing_in_target == [declared]
    assert drift.reference_ahead == [pending]


class _RowsConn:
    """Answers ``fetch`` with canned rows, recording the SQL it was asked."""

    def __init__(self, rows):
        self.rows = rows
        self.sql: list[str] = []

    async def fetch(self, sql):
        self.sql.append(sql)
        return self.rows


_DEF = "CREATE UNIQUE INDEX uq_x ON public.t USING btree (a)"


async def test_snapshot_unique_indexes_marks_an_invalid_index():
    """An invalid index (a failed ``CONCURRENTLY`` build, which ``IF NOT EXISTS`` then
    never rebuilds) carries a marker in its def, so a valid reference copy mismatches."""
    conn = _RowsConn(
        [
            {"table_name": "t", "index_name": "uq_x", "def": _DEF, "valid": True},
            {"table_name": "t", "index_name": "uq_y", "def": _DEF, "valid": False},
        ]
    )
    snap = await snapshot_unique_indexes(conn)
    assert snap[IndexKey(table="t", name="uq_x")] == _DEF
    assert snap[IndexKey(table="t", name="uq_y")] == _DEF + INVALID_INDEX_MARKER
    valid = {IndexKey(table="t", name="uq_y"): _DEF}
    drift = diff_defs(kind="unique_index", reference=valid, target=snap)
    assert [k for k, _, _ in drift.mismatched] == [IndexKey(table="t", name="uq_y")]


async def test_snapshot_unique_indexes_excludes_only_constraint_backing_indexes():
    """An FK sets ``conindid`` too, to the referenced table's unique index; only a
    PK/UNIQUE/EXCLUDE constraint's own index is covered by the constraint snapshot."""
    conn = _RowsConn([])
    await snapshot_unique_indexes(conn)
    sql = " ".join(conn.sql[0].split())
    assert "c.contype IN ('p', 'u', 'x')" in sql
    assert "c.conrelid = i.indrelid" in sql


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


@pytest.mark.parametrize(
    ("table", "name"),
    [
        ("identifiers", "uq_identifiers_cannabis_observer"),
        ("roles", "uq_role_structural"),
        ("roles", "uq_role_org_title"),
        ("role_assignments", "uq_role_assignment_person_role_start"),
    ],
)
def test_real_schema_sql_declares_the_warn_dont_abort_indexes(table, name):
    """The four unique indexes whose ``DO`` block warns and skips on a duplicate
    (#398/#748): their absence from prod is real drift, never a pending deploy."""
    deployed = DeployedSchema.from_sql(SCHEMA_PATH.read_text())
    assert deployed.declares("unique_index", IndexKey(table=table, name=name))


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
async def test_snapshot_functions_captures_touch_functions(db_pool):
    async with db_pool.acquire() as conn:
        snap = await snapshot_functions(conn)
    sigs = {k.signature for k in snap}
    # #327 touch functions are ours to guard and must be present.
    assert "touch_parent_on_link_change()" in sigs
    assert "touch_parent_on_contact_change()" in sigs
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
    # #327 touch trigger present, keyed on (table, name).
    link_trg = TriggerKey(table="links", name="trg_touch_entity_on_link_change")
    assert link_trg in snap
    assert "TRIGGER" in snap[link_trg]
    # No internal FK/constraint-enforcement triggers leak in (they start with RI_).
    assert not any(k.name.startswith("RI_ConstraintTrigger") for k in snap)
    assert not diff_defs(kind="trigger", reference=snap, target=dict(snap)).has_drift


@pytest.mark.integration
async def test_snapshot_unique_indexes_against_live_db(db_pool):
    """Standalone unique indexes only: no PK, no index backing a UNIQUE constraint
    (the constraint snapshot has those), every one valid on a fresh apply."""
    async with db_pool.acquire() as conn:
        snap = await snapshot_unique_indexes(conn)
        backing = {
            r["name"]
            for r in await conn.fetch(
                "SELECT i.relname AS name FROM pg_constraint c "
                "JOIN pg_class i ON i.oid = c.conindid "
                "JOIN pg_namespace n ON n.oid = i.relnamespace "
                "WHERE n.nspname = 'public' AND c.contype IN ('p', 'u')"
            )
        }
    assert IndexKey(table="identifiers", name="uq_identifiers_cannabis_observer") in snap
    assert IndexKey(table="roles", name="uq_role_org_title") in snap
    assert backing and not backing & {k.name for k in snap}
    assert all(d.startswith("CREATE UNIQUE INDEX") for d in snap.values())
    assert not any(d.endswith(INVALID_INDEX_MARKER) for d in snap.values())
    assert not diff_defs(kind="unique_index", reference=snap, target=dict(snap)).has_drift


@pytest.mark.integration
async def test_snapshot_keeps_a_unique_index_an_fk_references(db_pool):
    """An FK's ``conindid`` points at the referenced unique index; that index is not
    constraint-backed and must stay in the snapshot. Built in a rolled-back txn."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(
                "CREATE TABLE _pm816_parent (code TEXT NOT NULL);"
                "CREATE UNIQUE INDEX _pm816_uq_code ON _pm816_parent (code);"
                "CREATE TABLE _pm816_child (code TEXT REFERENCES _pm816_parent (code));"
            )
            snap = await snapshot_unique_indexes(conn)
        finally:
            await tr.rollback()
    assert IndexKey(table="_pm816_parent", name="_pm816_uq_code") in snap


@pytest.mark.integration
async def test_schema_sql_declares_every_live_object(db_pool):
    """The matching rules hold on the real schema: of the test DB's constraints
    (most named by Postgres, never written in schema.sql), functions, triggers and
    unique indexes,
    none reads as ahead of the schema.sql that built it. A failure names either a
    rule gap or a stray branch's ``apply-schema.sh --test`` still on the shared
    test DB."""
    async with db_pool.acquire() as conn:
        snaps = {
            "constraint": await snapshot_constraints(conn),
            "function": await snapshot_functions(conn),
            "trigger": await snapshot_triggers(conn),
            "unique_index": await snapshot_unique_indexes(conn),
        }
    deployed = DeployedSchema.from_sql(SCHEMA_PATH.read_text())
    ahead = sorted(
        f"{kind}.{k.label}"
        for kind, snap in snaps.items()
        for k in snap
        if not deployed.declares(kind, k)
    )
    assert ahead == []
