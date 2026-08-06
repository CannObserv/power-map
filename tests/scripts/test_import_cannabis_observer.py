"""Guards on scripts/import_cannabis_observer.py (#402).

The script resolves `DATABASE_URL` — **production**, from any directory — and
before #402 a bare invocation applied schema DDL and committed a full CSV
import with no confirmation and no dry run. These tests pin the three
properties that fix carries: dry run by default, DDL only when asked for
explicitly, and a visible target.

No DB: the connection and the import pipeline are both faked, so what is under
test is the gating, not the import itself.
"""

from pathlib import Path

import pytest

from scripts import import_cannabis_observer as cli

DSN = "postgresql://import_user:s3kr3t@import.example.invalid:5432/importdb"
SECRET = "s3kr3t"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeTransaction:
    """Records whether the block it wrapped exited with an exception."""

    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn

    async def __aenter__(self) -> "FakeTransaction":
        self.conn.transactions_opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.conn.rolled_back = exc_type is not None
        return False


class FakeConn:
    """Minimal asyncpg.Connection stand-in."""

    def __init__(self) -> None:
        self.transactions_opened = 0
        self.rolled_back: bool | None = None
        self.closed = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def csvs(tmp_path: Path) -> dict[str, Path]:
    """Three existing (empty) CSV paths — the script stats them before connecting."""
    paths = {}
    for name in ("orgs", "people", "roles"):
        p = tmp_path / f"{name}.csv"
        p.write_text("id\n")
        paths[name] = p
    return paths


@pytest.fixture
def harness(monkeypatch, csvs):
    """Fake out the connection, the pipeline and the schema apply.

    Returns a callable taking argv flags and returning the recorded calls.
    """
    calls: dict[str, object] = {"connected_to": None, "schema_applied": 0, "imports": []}
    conn = FakeConn()

    async def fake_connect(dsn):
        calls["connected_to"] = dsn
        return conn

    async def fake_apply_schema(c):
        calls["schema_applied"] += 1

    async def fake_run_import(c, config):
        calls["imports"].append(config)
        return {"orgs": 1, "people": 2, "roles": 3}

    monkeypatch.setattr(cli.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(cli, "apply_schema", fake_apply_schema)
    monkeypatch.setattr(cli, "run_import", fake_run_import)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def run(*flags: str) -> dict:
        argv = [
            "--orgs",
            str(csvs["orgs"]),
            "--people",
            str(csvs["people"]),
            "--roles",
            str(csvs["roles"]),
            *flags,
        ]
        cli.main(argv)
        return calls

    run.calls = calls  # type: ignore[attr-defined]
    run.conn = conn  # type: ignore[attr-defined]
    return run


# --------------------------------------------------------------------------- #
# The write gate
# --------------------------------------------------------------------------- #


def test_bare_invocation_is_a_dry_run(harness, monkeypatch):
    """No --execute: the import runs inside a transaction that is rolled back."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness()
    assert len(calls["imports"]) == 1, "dry run should still exercise the pipeline"
    assert harness.conn.transactions_opened == 1, "dry run must wrap the import in a transaction"
    assert harness.conn.rolled_back is True, "dry run must roll the transaction back"


def test_execute_commits(harness, monkeypatch):
    """--execute: the import is not unwound."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness("--execute")
    assert len(calls["imports"]) == 1
    assert harness.conn.rolled_back is not True, "--execute must not roll back"


def test_dry_run_keeps_addresses_local(harness, monkeypatch):
    """A preview must not spend the external validator's rate-limited quota (#402)."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness()
    assert calls["imports"][0].local_addresses_only is True


def test_execute_uses_the_address_service(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness("--execute")
    assert calls["imports"][0].local_addresses_only is False


def test_dry_run_says_addresses_were_parsed_locally(harness, monkeypatch, capsys):
    """The preview differs from the commit here — say so rather than imply parity."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    assert "local" in "".join(capsys.readouterr()).lower()


def test_bare_invocation_does_not_apply_schema(harness, monkeypatch):
    """The silent DDL is the sharpest half of #402 — it must not fire by default."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness()
    assert calls["schema_applied"] == 0


def test_execute_alone_does_not_apply_schema(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness("--execute")
    assert calls["schema_applied"] == 0


def test_apply_schema_flag_applies_schema(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    calls = harness("--execute", "--apply-schema")
    assert calls["schema_applied"] == 1


def test_apply_schema_requires_execute(harness, monkeypatch):
    """DDL inside a rolled-back dry run would be a lie — reject the combination."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    with pytest.raises(SystemExit) as exc:
        harness("--apply-schema")
    assert exc.value.code != 0
    assert harness.calls["schema_applied"] == 0
    assert harness.calls["imports"] == []


# --------------------------------------------------------------------------- #
# Target resolution & echo
# --------------------------------------------------------------------------- #


def test_database_url_flag_overrides_env(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    other = "postgresql://u:p@other.example.invalid:5432/otherdb"
    calls = harness("--database-url", other)
    assert calls["connected_to"] == other


def test_missing_dsn_exits(harness, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        harness()


def test_target_is_echoed_without_the_password(harness, monkeypatch, capsys):
    """A run must be attributable to a database in scrollback — but never leak the DSN."""
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "importdb" in combined, "the target database name should be visible"
    assert "import.example.invalid" in combined
    assert SECRET not in combined, "the password must never be echoed"


def test_dry_run_is_announced(harness, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    combined = "".join(capsys.readouterr())
    assert "--execute" in combined, "a dry run must say how to commit"


# --------------------------------------------------------------------------- #
# Regression: the connection is always closed
# --------------------------------------------------------------------------- #


def test_connection_closed_on_dry_run(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    assert harness.conn.closed is True


def test_dry_run_rollback_sentinel_never_escapes(harness, monkeypatch):
    """The rollback is driven by an internal exception; it must not reach the caller.

    Reaching the end of this test *is* the assertion: an escaped
    ``_DryRunRollback`` would surface as an error here.
    """
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
