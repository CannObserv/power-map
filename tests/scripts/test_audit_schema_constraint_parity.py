"""Orchestration + exit-code contract for the parity audit script (#315).

The drift diff itself is tested in ``tests/core/test_schema_parity.py``; here we
pin the pieces the systemd timer depends on — ``run()``'s OK-vs-drift branching
and drift-count return, the two misconfiguration guards (empty reference,
reference == target) that keep the monitor from passing vacuously, and
``main()``'s exit-3 contract. No live DB: ``asyncpg.connect`` and
``snapshot_constraints`` are stubbed so each URL resolves to an injected
snapshot.
"""

import asyncio
import logging
import sys

import pytest

import scripts.audit_schema_constraint_parity as audit
from src.core.schema_parity import ConstraintKey

_CK = ConstraintKey(table="entity_events", name="fk_addr")
_SET_NULL = "FOREIGN KEY (a) REFERENCES addresses(id) ON DELETE SET NULL"
_NO_ACTION = "FOREIGN KEY (a) REFERENCES addresses(id)"

_REF = "postgres://u:pw@ref-host:5432/refdb"
_PROD = "postgres://u:pw@prod-host:5432/proddb"


class _FakeConn:
    """Stands in for an asyncpg connection; carries the DSN it was opened with."""

    def __init__(self, dsn):
        self.dsn = dsn
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def stub_dbs(monkeypatch):
    """Wire ``asyncpg.connect`` + ``snapshot_constraints`` to per-DSN snapshots.

    Returns a setter taking ``{dsn: snapshot_dict}``; ``run()`` then sees each
    URL resolve to its injected constraint snapshot with no real I/O.
    """
    snapshots: dict[str, dict] = {}

    async def fake_connect(dsn):
        return _FakeConn(dsn)

    async def fake_snapshot(conn):
        return snapshots[conn.dsn]

    monkeypatch.setattr(audit.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(audit, "snapshot_constraints", fake_snapshot)

    def _set(mapping):
        snapshots.clear()
        snapshots.update(mapping)

    return _set


def test_run_returns_zero_when_parity(stub_dbs):
    snap = {_CK: _SET_NULL}
    stub_dbs({_REF: dict(snap), _PROD: dict(snap)})
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 0


def test_run_returns_drift_count_on_mismatch(stub_dbs):
    stub_dbs({_REF: {_CK: _SET_NULL}, _PROD: {_CK: _NO_ACTION}})
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 1


def test_run_counts_missing_and_mismatched(stub_dbs):
    other = ConstraintKey(table="t", name="ck_other")
    stub_dbs(
        {
            _REF: {_CK: _SET_NULL, other: "CHECK (x > 0)"},
            _PROD: {_CK: _NO_ACTION},  # `other` missing + `_CK` differs
        }
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 2


def test_run_ignores_target_only_constraints(stub_dbs):
    prod_only = ConstraintKey(table="t", name="leftover")
    stub_dbs(
        {
            _REF: {_CK: _SET_NULL},
            _PROD: {_CK: _SET_NULL, prod_only: "CHECK (true)"},
        }
    )
    assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 0


def test_run_fails_on_empty_reference(stub_dbs, caplog):
    """Blank/wrong reference DB → refuse to report parity against nothing."""
    stub_dbs({_REF: {}, _PROD: {_CK: _SET_NULL}})
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(audit.run(reference_url=_REF, target_url=_PROD)) == 1
    assert "MISCONFIGURED" in caplog.text


def test_run_fails_when_reference_is_same_db_as_target(stub_dbs, caplog):
    """Same user@host/db on both sides → would compare prod to itself.

    Detection is on the redacted user@host/db, so a differing password or query
    string must not defeat it. The guard fires before any snapshot, so none is
    registered.
    """
    stub_dbs({})
    same = "postgres://u:pw@host:5432/db?sslmode=require"
    other_creds = "postgres://u:DIFFERENT@host:5432/db?sslmode=disable"
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(audit.run(reference_url=same, target_url=other_creds)) == 1
    assert "same database" in caplog.text


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
