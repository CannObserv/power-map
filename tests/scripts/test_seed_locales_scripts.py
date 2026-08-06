"""Unit tests for the locale/script seed helpers (no DB).

Skipped when the `seed` dependency group isn't installed (default
runtime env). Run via `uv run --group seed pytest tests/scripts/...`.

The CLI-gate tests at the bottom pin #402: before it, a bare invocation
upserted into `DATABASE_URL` — production, from any directory.
"""

import pytest

pytest.importorskip("langcodes")
pytest.importorskip("pycountry")

from scripts import seed_locales_scripts as cli  # noqa: E402
from scripts.seed_locales_scripts import (  # noqa: E402
    enumerate_bcp47_locales,
    enumerate_iso15924_scripts,
)


def test_enumerate_bcp47_locales_yields_dict_records():
    rows = list(enumerate_bcp47_locales())
    assert len(rows) > 1000, f"expected at least 1000 CLDR locales, got {len(rows)}"
    sample = rows[0]
    assert {"code", "language", "script", "region", "display_name"} <= set(sample)


def test_enumerate_bcp47_locales_includes_common_codes():
    """Sanity check: well-known codes resolve in the enumeration."""
    rows = list(enumerate_bcp47_locales())
    by_code = {r["code"]: r for r in rows}
    for code in ("en", "es", "ja", "is"):
        assert code in by_code, f"common locale {code} missing from enumeration"


def test_enumerate_bcp47_locales_codes_unique():
    rows = list(enumerate_bcp47_locales())
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate code in seed enumeration"


def test_enumerate_bcp47_locales_display_name_non_empty():
    rows = list(enumerate_bcp47_locales())
    empty = [r for r in rows if not r["display_name"]]
    assert not empty, f"{len(empty)} locales had empty display_name"


def test_enumerate_iso15924_scripts_full_set():
    rows = list(enumerate_iso15924_scripts())
    assert len(rows) >= 180, f"expected ~200 ISO 15924 codes, got {len(rows)}"
    sample = rows[0]
    assert {"code", "numeric_code", "name"} <= set(sample)


def test_enumerate_iso15924_scripts_includes_common_codes():
    rows = list(enumerate_iso15924_scripts())
    by_code = {r["code"]: r for r in rows}
    for code in ("Latn", "Hans", "Hant", "Cyrl", "Arab"):
        assert code in by_code, f"common script {code} missing from enumeration"


def test_iso15924_numeric_codes_unique():
    rows = list(enumerate_iso15924_scripts())
    codes = [r["numeric_code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate numeric_code in seed enumeration"


def test_iso15924_codes_are_four_letter():
    rows = list(enumerate_iso15924_scripts())
    for r in rows:
        assert len(r["code"]) == 4, f"non-4-letter code: {r['code']!r}"


# --------------------------------------------------------------------------- #
# CLI write gate (#402)
# --------------------------------------------------------------------------- #

DSN = "postgresql://seed_user:s3kr3t@seed.example.invalid:5432/seeddb"
SECRET = "s3kr3t"


class FakeTransaction:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn

    async def __aenter__(self) -> "FakeTransaction":
        self.conn.transactions_opened += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeConn:
    """Minimal asyncpg.Connection stand-in: records writes, reads back nothing."""

    def __init__(self) -> None:
        self.executemany_calls: list[str] = []
        self.transactions_opened = 0
        self.closed = False

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def fetch(self, sql: str, *args):
        return []

    async def executemany(self, sql: str, payload) -> None:
        self.executemany_calls.append(sql)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def harness(monkeypatch):
    """Fake the connection; return a callable that runs the CLI with flags."""
    conn = FakeConn()
    connected: dict[str, object] = {"dsn": None}

    async def fake_connect(dsn):
        connected["dsn"] = dsn
        return conn

    monkeypatch.setattr(cli.asyncpg, "connect", fake_connect)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def run(*flags: str) -> FakeConn:
        cli.main(list(flags))
        return conn

    run.conn = conn  # type: ignore[attr-defined]
    run.connected = connected  # type: ignore[attr-defined]
    return run


def test_bare_invocation_writes_nothing(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    conn = harness()
    assert conn.executemany_calls == [], "a bare invocation must not upsert"


def test_execute_upserts_both_tables(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    conn = harness("--execute")
    assert len(conn.executemany_calls) == 2, "expected one upsert per lookup table"
    assert any("iso15924_scripts" in sql for sql in conn.executemany_calls)
    assert any("bcp47_locales" in sql for sql in conn.executemany_calls)


def test_dry_run_is_announced(harness, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    combined = "".join(capsys.readouterr())
    assert "--execute" in combined, "a dry run must say how to commit"


def test_target_is_echoed_without_the_password(harness, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DSN)
    harness()
    combined = "".join(capsys.readouterr())
    assert "seeddb" in combined
    assert "seed.example.invalid" in combined
    assert SECRET not in combined, "the password must never be echoed"


def test_database_url_flag_overrides_env(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    other = "postgresql://u:p@other.example.invalid:5432/otherdb"
    harness("--database-url", other)
    assert harness.connected["dsn"] == other


def test_missing_dsn_exits(harness, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        harness()


def test_connection_closed(harness, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    conn = harness()
    assert conn.closed is True
