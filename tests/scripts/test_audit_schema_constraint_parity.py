"""Orchestration + exit-code contract for the parity audit script (#315, #331).

The drift diff itself is tested in ``tests/core/test_schema_parity.py``; here we
pin the pieces the systemd timer depends on — ``run()``'s per-kind OK-vs-drift
branching and summed drift-count return across constraints/functions/triggers,
the PG-major version skip for the version-sensitive kinds, the two
misconfiguration guards (empty reference, reference == target) that keep the
monitor from passing vacuously, and ``main()``'s exit-3 contract. No live DB:
``asyncpg.connect`` and the ``snapshot_*`` helpers are stubbed so each URL
resolves to an injected per-kind snapshot.
"""

import asyncio
import logging
import sys

import pytest

import scripts.audit_schema_constraint_parity as audit
from src.core.schema_parity import ConstraintKey, FunctionKey, TriggerKey

_CK = ConstraintKey(table="entity_events", name="fk_addr")
_SET_NULL = "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL"
_NO_ACTION = "FOREIGN KEY (a) REFERENCES addresses(id)"

_FK = FunctionKey(signature="touch_parent_on_link_change()")
_TK = TriggerKey(table="links", name="trg_touch_entity_on_link_change")

_REF = "postgres://u:pw@ref-host:5432/refdb"
_PROD = "postgres://u:pw@prod-host:5432/proddb"


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


def test_run_returns_zero_when_parity(stub_dbs):
    snap = _snap(constraint={_CK: _SET_NULL}, function={_FK: "f"}, trigger={_TK: "t"})
    stub_dbs({_REF: snap, _PROD: {k: dict(v) for k, v in snap.items()}})
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 0
    # Same major → all three kinds snapshotted on both DBs.
    assert sorted(stub_dbs.calls) == sorted(["constraint", "function", "trigger"] * 2)


def test_run_returns_drift_count_on_constraint_mismatch(stub_dbs):
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _NO_ACTION}),
        }
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 1


def test_run_sums_drift_across_all_kinds(stub_dbs):
    """A missing constraint + a drifted function + a missing trigger = 3."""
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}, function={_FK: "v2"}, trigger={_TK: "t"}),
            _PROD: _snap(constraint={}, function={_FK: "v1"}, trigger={}),
        }
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 3


def test_run_ignores_target_only_objects(stub_dbs):
    prod_only_fn = FunctionKey(signature="prod_only()")
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _SET_NULL}, function={prod_only_fn: "x"}),
        }
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 0


def test_run_skips_version_sensitive_kinds_on_major_mismatch(stub_dbs, caplog):
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
        assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 0
    assert "function parity SKIPPED" in caplog.text
    assert "trigger parity SKIPPED" in caplog.text
    # Skipped kinds must not be snapshotted at all (no wasted pg_get_*def query) —
    # only the constraint snapshot runs, once per DB.
    assert set(stub_dbs.calls) == {"constraint"}
    assert stub_dbs.calls.count("constraint") == 2


def test_run_still_diffs_constraints_on_major_mismatch(stub_dbs):
    """Constraints are version-stable — a major mismatch must not suppress them."""
    stub_dbs(
        {
            _REF: _snap(constraint={_CK: _SET_NULL}),
            _PROD: _snap(constraint={_CK: _NO_ACTION}),
        },
        major_map={_REF: 16, _PROD: 15},
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 1


def test_run_fails_on_empty_reference(stub_dbs, caplog):
    """Blank/wrong reference DB (no constraints) → refuse to report parity."""
    stub_dbs({_REF: _snap(), _PROD: _snap(constraint={_CK: _SET_NULL})})
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 1
    assert "MISCONFIGURED" in caplog.text


def test_run_fails_when_reference_is_same_db_as_target(stub_dbs, caplog):
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
        assert asyncio.run(audit.run(reference_url=ref, target_url=tgt)) == 1
    assert "same database" in caplog.text


def test_run_fails_on_implicit_vs_explicit_default_port(stub_dbs, caplog):
    """Implicit port and explicit :5432 on the same DB are the same DB — must trip."""
    stub_dbs({})
    implicit = "postgres://u:pw@host/db"  # port defaults to 5432
    explicit = "postgres://u:pw@host:5432/db"
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(audit.run(reference_url=implicit, target_url=explicit)) == 1
    assert "same database" in caplog.text


def test_run_allows_same_host_different_port(stub_dbs):
    """Same host + dbname but a **different port** is a distinct DB — must not trip.

    _redact drops the port, so reusing it for the guard would false-trip here;
    keying on (host, port, dbname) keeps these two distinct.
    """
    ref = "postgres://u:pw@host:5432/db"
    tgt = "postgres://u:pw@host:5433/db"
    stub_dbs({ref: _snap(constraint={_CK: _SET_NULL}), tgt: _snap(constraint={_CK: _SET_NULL})})
    assert asyncio.run(audit.run(reference_url=ref, target_url=tgt)) == 0


def test_main_exits_3_on_drift(monkeypatch):
    """Drift (or misconfig) exit code is 3 — distinct from argparse errors (exit 2)."""

    async def fake_run(*, reference_url, target_url):
        return 1

    monkeypatch.setattr(audit, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["audit", "--target-url", _PROD, "--reference-url", _REF])
    with pytest.raises(SystemExit) as excinfo:
        audit.main()
    assert excinfo.value.code == 3


def test_main_exits_clean_on_parity(monkeypatch):
    async def fake_run(*, reference_url, target_url):
        return 0

    monkeypatch.setattr(audit, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["audit", "--target-url", _PROD, "--reference-url", _REF])
    audit.main()  # no SystemExit


def test_main_errors_without_target(monkeypatch):
    """Missing target (no DATABASE_URL, no flag) is an argparse usage error (exit 2)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["audit", "--reference-url", _REF])
    with pytest.raises(SystemExit) as excinfo:
        audit.main()
    assert excinfo.value.code == 2
