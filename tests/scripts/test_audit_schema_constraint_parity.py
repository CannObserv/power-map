"""Orchestration + exit-code contract for the parity audit script (#315, #331, #632).

The drift diff itself is tested in ``tests/core/test_schema_parity.py``; here we
pin the pieces the systemd timer depends on — ``run()``'s per-kind OK-vs-drift
branching and summed drift-count return across constraints/functions/triggers,
the PG-major version skip for the version-sensitive kinds, the two
misconfiguration guards (empty reference, reference == target) that keep the
monitor from passing vacuously, the reference-ahead classification against the
deployed ``schema.sql`` and its per-object run streak (#632), and ``main()``'s
exit codes (3 failure, 4 reference ahead only). No live DB: ``asyncpg.connect``
and the ``snapshot_*`` helpers are stubbed so each URL resolves to an injected
per-kind snapshot, and the deployed schema is SQL text.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import pytest

import scripts.audit_schema_constraint_parity as audit
from src.core.schema_parity import ConstraintKey, DeployedSchema, FunctionKey, TriggerKey

_CK = ConstraintKey(table="entity_events", name="fk_addr")
_SET_NULL = "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL"
_NO_ACTION = "FOREIGN KEY (a) REFERENCES addresses(id)"

_FK = FunctionKey(signature="touch_parent_on_link_change()")
_TK = TriggerKey(table="links", name="trg_touch_entity_on_link_change")

_REF = "postgres://u:pw@ref-host:5432/refdb"
_PROD = "postgres://u:pw@prod-host:5432/proddb"

#: Declares _CK, _FK and _TK, so their absence from the target is real drift.
_DEPLOYED = DeployedSchema.from_sql("""
CREATE TABLE IF NOT EXISTS entity_events (a TEXT, CONSTRAINT fk_addr FOREIGN KEY (a)
    REFERENCES addresses(id) ON DELETE SET NULL);
CREATE OR REPLACE FUNCTION touch_parent_on_link_change() RETURNS trigger AS $$ $$;
CREATE OR REPLACE TRIGGER trg_touch_entity_on_link_change AFTER INSERT ON links
    FOR EACH ROW EXECUTE FUNCTION touch_parent_on_link_change();
""")

#: The deployed schema.sql of 2026-10-09 00:01 UTC, as far as the incident goes:
#: PR #629's two functions and three triggers were on the test DB (the reference)
#: but not yet in the main checkout, and so not in prod either.
_PRE_629_DEPLOYED = DeployedSchema.from_sql("""
CREATE TABLE IF NOT EXISTS identifiers (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS people (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY);
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$ $$;
CREATE OR REPLACE TRIGGER trg_updated_at_people BEFORE UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
""")
_INCIDENT_FUNCTIONS = {
    FunctionKey(signature="lock_identifier_entity()"): "f1",
    FunctionKey(signature="refuse_delete_while_linked()"): "f2",
}
_INCIDENT_TRIGGERS = {
    TriggerKey(table="identifiers", name="trg_identifiers_entity"): "t1",
    TriggerKey(table="people", name="trg_people_inbound_event_links"): "t2",
    TriggerKey(table="organizations", name="trg_organizations_inbound_event_links"): "t3",
}
_PEOPLE_PK = ConstraintKey(table="people", name="people_pkey")


class _Ver:
    def __init__(self, major):
        self.major = major


class _FakeConn:
    """Stands in for an asyncpg connection; carries the DSN it was opened with."""

    def __init__(self, dsn, major):
        self.dsn = dsn
        self._major = major
        self.closed = False

    def get_server_version(self):
        return _Ver(self._major)

    async def close(self):
        self.closed = True


def _snap(*, constraint=None, function=None, trigger=None):
    """Build a per-kind snapshot bundle, defaulting each kind to empty."""
    return {
        "constraint": constraint or {},
        "function": function or {},
        "trigger": trigger or {},
    }


@pytest.fixture
def stub_dbs(monkeypatch):
    """Wire ``asyncpg.connect`` + the three ``snapshot_*`` helpers to per-DSN data.

    Returns a setter taking ``{dsn: bundle}`` (bundle from ``_snap``) and an
    optional ``majors={dsn: int}``; ``run()`` then sees each URL resolve to its
    injected per-kind snapshot with no real I/O.
    """
    bundles: dict[str, dict] = {}
    majors: dict[str, int] = {}
    calls: list[str] = []  # kinds actually snapshotted, in call order

    async def fake_connect(dsn):
        return _FakeConn(dsn, majors.get(dsn, 16))

    def _snapshotter(kind):
        async def fake(conn):
            calls.append(kind)
            return bundles[conn.dsn][kind]

        return fake

    monkeypatch.setattr(audit.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(audit, "snapshot_constraints", _snapshotter("constraint"))
    monkeypatch.setattr(audit, "snapshot_functions", _snapshotter("function"))
    monkeypatch.setattr(audit, "snapshot_triggers", _snapshotter("trigger"))

    def _set(mapping, *, major_map=None):
        bundles.clear()
        bundles.update(mapping)
        majors.clear()
        if major_map:
            majors.update(major_map)
        calls.clear()

    # Expose the recorded snapshot calls so tests can assert which kinds ran.
    _set.calls = calls
    return _set


@pytest.fixture
def state_path(tmp_path):
    """Where the reference-ahead streaks persist between runs, per test."""
    return tmp_path / "schema_parity" / "reference_ahead.json"


@pytest.fixture
def run_audit(state_path):
    """``audit.run`` with this test's state file and, by default, ``_DEPLOYED``."""

    def _run(reference_url=_REF, target_url=_PROD, *, deployed=_DEPLOYED, escalate_after=3):
        return asyncio.run(
            audit.run(
                reference_url=reference_url,
                target_url=target_url,
                deployed=deployed,
                state_path=state_path,
                escalate_after=escalate_after,
            )
        )

    return _run


def test_run_returns_zero_when_parity(stub_dbs, run_audit):
    snap = _snap(constraint={_CK: _SET_NULL}, function={_FK: "f"}, trigger={_TK: "t"})
    stub_dbs({_REF: snap, _PROD: {k: dict(v) for k, v in snap.items()}})
    assert run_audit().exit_code == 0
    # Same major → all three kinds snapshotted on both DBs.
    assert sorted(stub_dbs.calls) == sorted(["constraint", "function", "trigger"] * 2)


def test_run_returns_drift_count_on_constraint_mismatch(stub_dbs, run_audit):
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _NO_ACTION}),
        }
    )
    assert run_audit().drift_count == 1


def test_run_sums_drift_across_all_kinds(stub_dbs, run_audit):
    """A missing constraint + a drifted function + a missing trigger = 3."""
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}, function={_FK: "v2"}, trigger={_TK: "t"}),
            _PROD: _snap(constraint={}, function={_FK: "v1"}, trigger={}),
        }
    )
    assert run_audit().drift_count == 3


def test_run_ignores_target_only_objects(stub_dbs, run_audit):
    prod_only_fn = FunctionKey(signature="prod_only()")
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _SET_NULL}, function={prod_only_fn: "x"}),
        }
    )
    assert run_audit().exit_code == 0


def test_run_skips_version_sensitive_kinds_on_major_mismatch(stub_dbs, run_audit, caplog):
    """Different PG majors → function/trigger diffs skipped; constraint still runs."""
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}, function={_FK: "v2"}, trigger={_TK: "t2"}),
            _PROD: _snap(constraint={_CK: _SET_NULL}, function={_FK: "v1"}, trigger={_TK: "t1"}),
        },
        major_map={_REF: 16, _PROD: 15},
    )
    with caplog.at_level(logging.WARNING):
        # Function + trigger drift would be 2, but both are skipped → 0.
        assert run_audit().exit_code == 0
    assert "function parity SKIPPED" in caplog.text
    assert "trigger parity SKIPPED" in caplog.text
    # Skipped kinds must not be snapshotted at all (no wasted pg_get_*def query) —
    # only the constraint snapshot runs, once per DB.
    assert set(stub_dbs.calls) == {"constraint"}
    assert stub_dbs.calls.count("constraint") == 2


def test_run_still_diffs_constraints_on_major_mismatch(stub_dbs, run_audit):
    """Constraints are version-stable — a major mismatch must not suppress them."""
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _NO_ACTION}),
        },
        major_map={_REF: 16, _PROD: 15},
    )
    assert run_audit().drift_count == 1


def test_run_fails_on_empty_reference(stub_dbs, run_audit, caplog):
    """Blank/wrong reference DB (no constraints) → refuse to report parity."""
    stub_dbs({_REF: _snap(), _PROD: _snap(constraint={_CK: _SET_NULL})})
    with caplog.at_level(logging.WARNING):
        assert run_audit().exit_code == 3
    assert "MISCONFIGURED" in caplog.text


def test_run_fails_when_reference_is_same_db_as_target(stub_dbs, run_audit, caplog):
    """Same (host, port, dbname) on both sides → would compare prod to itself.

    Identity excludes user and credentials, so a **different user** on the same
    physical DB (this project reaches co_pm_db_production as both the app and the
    migrations user) must still trip the guard — and differing password/sslmode
    must not defeat it. The guard fires before any snapshot, so none is registered.
    """
    stub_dbs({})
    ref = "postgres://app_user:pw@host:5432/proddb?sslmode=require"
    tgt = "postgres://migrations_user:OTHER@host:5432/proddb?sslmode=disable"
    with caplog.at_level(logging.WARNING):
        assert run_audit(ref, tgt).exit_code == 3
    assert "same database" in caplog.text


def test_run_fails_on_implicit_vs_explicit_default_port(stub_dbs, run_audit, caplog):
    """Implicit port and explicit :5432 on the same DB are the same DB — must trip."""
    stub_dbs({})
    implicit = "postgres://u:pw@host/db"  # port defaults to 5432
    explicit = "postgres://u:pw@host:5432/db"
    with caplog.at_level(logging.WARNING):
        assert run_audit(implicit, explicit).exit_code == 3
    assert "same database" in caplog.text


def test_run_allows_same_host_different_port(stub_dbs, run_audit):
    """Same host + dbname but a **different port** is a distinct DB — must not trip.

    _redact drops the port, so reusing it for the guard would false-trip here;
    keying on (host, port, dbname) keeps these two distinct.
    """
    ref = "postgres://u:pw@host:5432/db"
    tgt = "postgres://u:pw@host:5433/db"
    stub_dbs({ref: _snap(constraint={_CK: _SET_NULL}), tgt: _snap(constraint={_CK: _SET_NULL})})
    assert run_audit(ref, tgt).exit_code == 0


def _fake_run(result):
    async def fake_run(**_kwargs):
        return result

    return fake_run


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (audit.AuditResult(drift_count=1), 3),
        (audit.AuditResult(misconfigured=True), 3),
        (audit.AuditResult(ahead=("function.f()",), escalated=("function.f()",)), 3),
        (audit.AuditResult(drift_count=1, ahead=("function.f()",)), 3),
        (audit.AuditResult(ahead=("function.f()",)), 4),
    ],
)
def test_main_exit_codes(monkeypatch, tmp_path, result, code):
    """3 = failure (drift, misconfig, escalated ahead) — distinct from argparse's 2;
    4 = reference ahead only, which the unit's ``SuccessExitStatus=4`` treats as
    success so it stays visible in the journal without turning the unit red."""
    monkeypatch.setattr(audit, "run", _fake_run(result))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--target-url",
            _PROD,
            "--reference-url",
            _REF,
            "--state-file",
            str(tmp_path / "s.json"),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        audit.main()
    assert excinfo.value.code == code


def test_main_exits_clean_on_parity(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "run", _fake_run(audit.AuditResult()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--target-url",
            _PROD,
            "--reference-url",
            _REF,
            "--state-file",
            str(tmp_path / "s.json"),
        ],
    )
    audit.main()  # no SystemExit


def test_main_passes_the_deployed_schema_and_state(monkeypatch, tmp_path):
    """``--deployed-schema`` / ``--state-file`` / ``--escalate-after`` reach ``run``;
    the deployed schema is read from the file named."""
    seen = {}

    async def fake_run(**kwargs):
        seen.update(kwargs)
        return audit.AuditResult()

    sql = tmp_path / "schema.sql"
    sql.write_text("CREATE OR REPLACE FUNCTION only_here() RETURNS int AS $$ $$;")
    state = tmp_path / "s.json"
    monkeypatch.setattr(audit, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit",
            "--target-url",
            _PROD,
            "--reference-url",
            _REF,
            "--deployed-schema",
            str(sql),
            "--state-file",
            str(state),
            "--escalate-after",
            "5",
        ],
    )
    audit.main()
    assert seen["deployed"].declares("function", FunctionKey(signature="only_here()"))
    assert seen["state_path"] == state
    assert seen["escalate_after"] == 5


def test_main_defaults_to_this_checkouts_schema_sql():
    """The unit runs from the main checkout, so its schema.sql is the deployed one."""
    assert audit.DEFAULT_DEPLOYED_SCHEMA == audit.SCHEMA_PATH


def test_main_rejects_a_negative_escalate_after(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit", "--target-url", _PROD, "--reference-url", _REF, "--escalate-after", "-1"],
    )
    with pytest.raises(SystemExit) as excinfo:
        audit.main()
    assert excinfo.value.code == 2


def test_main_errors_without_target(monkeypatch):
    """Missing target (no DATABASE_URL, no flag) is an argparse usage error (exit 2)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["audit", "--reference-url", _REF])
    with pytest.raises(SystemExit) as excinfo:
        audit.main()
    assert excinfo.value.code == 2


# --- reference ahead of the deployed schema (#632) ----------------------------


def _incident_dbs(stub_dbs):
    """2026-10-09 00:01 UTC: PR #629's objects on the reference, not on prod."""
    stub_dbs(
        {
            _REF: _snap(
                constraint={_PEOPLE_PK: "PRIMARY KEY (id)"},
                function=dict(_INCIDENT_FUNCTIONS),
                trigger=dict(_INCIDENT_TRIGGERS),
            ),
            _PROD: _snap(constraint={_PEOPLE_PK: "PRIMARY KEY (id)"}),
        }
    )


def test_2026_10_09_pending_deploy_warns_without_failing(stub_dbs, run_audit, caplog):
    """The incident: reference-only objects the deployed schema.sql does not declare
    are a pending deploy — named in a WARNING, exit 4, no drift."""
    _incident_dbs(stub_dbs)
    with caplog.at_level(logging.WARNING):
        result = run_audit(deployed=_PRE_629_DEPLOYED)
    assert result.drift_count == 0
    assert result.exit_code == 4
    assert result.ahead == (
        "function.lock_identifier_entity()",
        "function.refuse_delete_while_linked()",
        "trigger.identifiers.trg_identifiers_entity",
        "trigger.organizations.trg_organizations_inbound_event_links",
        "trigger.people.trg_people_inbound_event_links",
    )
    assert "reference ahead of the deployed schema (pending deploy?)" in caplog.text
    assert "refuse_delete_while_linked()" in caplog.text
    assert "DRIFT" not in caplog.text


def test_same_objects_once_deployed_but_missing_are_drift(stub_dbs, run_audit):
    """Once schema.sql declares them, the same absence from prod is real drift."""
    _incident_dbs(stub_dbs)
    deployed = DeployedSchema.from_sql(audit.SCHEMA_PATH.read_text())
    result = run_audit(deployed=deployed)
    assert result.drift_count == 5
    assert result.ahead == ()
    assert result.exit_code == 3


def test_reference_ahead_escalates_after_three_consecutive_runs(
    stub_dbs, run_audit, state_path, caplog
):
    """Runs 1–3 warn; the 4th consecutive run with the same object ahead fails —
    the reference carries an abandoned branch's schema, not a pending deploy."""
    _incident_dbs(stub_dbs)
    for _ in range(3):
        assert run_audit(deployed=_PRE_629_DEPLOYED).exit_code == 4
    with caplog.at_level(logging.WARNING):
        result = run_audit(deployed=_PRE_629_DEPLOYED)
    assert result.exit_code == 3
    assert result.escalated == result.ahead
    assert "ESCALATED" in caplog.text
    assert json.loads(state_path.read_text())["streaks"]["function.lock_identifier_entity()"] == 4


def test_escalate_after_is_configurable(stub_dbs, run_audit):
    _incident_dbs(stub_dbs)
    assert run_audit(deployed=_PRE_629_DEPLOYED, escalate_after=1).exit_code == 4
    assert run_audit(deployed=_PRE_629_DEPLOYED, escalate_after=1).exit_code == 3


def test_a_cleared_object_restarts_its_streak(stub_dbs, run_audit, state_path):
    """A deploy that lands clears the object; if it returns, it counts from 1 again."""
    _incident_dbs(stub_dbs)
    for _ in range(3):
        run_audit(deployed=_PRE_629_DEPLOYED)
    stub_dbs({_REF: _snap(constraint={_CK: _SET_NULL}), _PROD: _snap(constraint={_CK: _SET_NULL})})
    assert run_audit().exit_code == 0
    assert json.loads(state_path.read_text())["streaks"] == {}
    _incident_dbs(stub_dbs)
    assert run_audit(deployed=_PRE_629_DEPLOYED).exit_code == 4


def test_a_new_object_does_not_inherit_another_objects_streak(stub_dbs, run_audit, state_path):
    """Streaks are per object: a fresh pending deploy beside an old one warns."""
    stale = "function.abandoned()"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"streaks": {stale: 3}}))
    _incident_dbs(stub_dbs)
    result = run_audit(deployed=_PRE_629_DEPLOYED)
    assert result.exit_code == 4
    assert stale not in json.loads(state_path.read_text())["streaks"]


def test_skipped_kinds_keep_their_streaks(stub_dbs, run_audit, state_path):
    """A PG-major skip neither advances nor clears a function/trigger streak."""
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"streaks": {"function.f()": 2, "constraint.t.c": 2}}))
    stub_dbs(
        {_REF: _snap(constraint={_CK: _SET_NULL}), _PROD: _snap(constraint={_CK: _SET_NULL})},
        major_map={_REF: 16, _PROD: 15},
    )
    assert run_audit().exit_code == 0
    assert json.loads(state_path.read_text())["streaks"] == {"function.f()": 2}


def test_a_corrupt_state_file_restarts_the_streaks(stub_dbs, run_audit, state_path, caplog):
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not json")
    _incident_dbs(stub_dbs)
    with caplog.at_level(logging.WARNING):
        assert run_audit(deployed=_PRE_629_DEPLOYED).exit_code == 4
    assert "unreadable" in caplog.text
    assert json.loads(state_path.read_text())["streaks"]["function.lock_identifier_entity()"] == 1


def test_an_unwritable_state_file_fails(stub_dbs, run_audit, state_path, caplog):
    """Without its state the escalation can never fire, so that is a misconfiguration."""
    state_path.parent.mkdir(parents=True)
    state_path.mkdir()  # a directory where the file should be: the write fails
    _incident_dbs(stub_dbs)
    with caplog.at_level(logging.WARNING):
        result = run_audit(deployed=_PRE_629_DEPLOYED)
    assert result.misconfigured
    assert result.exit_code == 3
    assert "MISCONFIGURED" in caplog.text


def test_misconfiguration_leaves_the_state_alone(stub_dbs, run_audit, state_path):
    """A run that never diffed has no evidence either way: streaks stay as they were."""
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"streaks": {"function.f()": 2}}))
    stub_dbs({_REF: _snap(), _PROD: _snap()})
    assert run_audit().misconfigured
    assert json.loads(state_path.read_text())["streaks"] == {"function.f()": 2}


def test_unit_counts_reference_ahead_as_success():
    """Exit 4 is only a warning if the unit says so; otherwise it turns the unit red."""
    unit = Path(__file__).resolve().parents[2] / "infra" / "power-map-schema-parity.service"
    lines = unit.read_text().splitlines()
    assert f"SuccessExitStatus={audit.EXIT_REFERENCE_AHEAD}" in lines
