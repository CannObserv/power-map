"""Target labelling and DSN resolution for operational scripts (#399).

`redact_dsn` / `echo_target` shipped with #402; this covers what #399 adds on
top — the prod/test label and the uniform `--database-url` / `--test`
resolution every script now shares.

The label is the load-bearing part: production has **two** DSNs for one
database (`DATABASE_URL` as the app user, `MIGRATIONS_DATABASE_URL` as the
migrations user), so matching on the DSN string labels a migrations DSN
`unknown`. Matching keys on `(host, port, dbname)` instead.
"""

import argparse

import pytest

from scripts._dsn import (
    PRODUCTION,
    TEST,
    UNKNOWN,
    add_dsn_args,
    default_dsn,
    describe_dsn,
    resolve_dsn,
)

PROD = "postgresql://app_user:s3kr3t@db.example.invalid:25060/pmdb?sslmode=require"
MIGRATIONS = "postgresql://migrations_user:other@db.example.invalid:25060/pmdb?sslmode=require"
TEST_DSN = "postgresql://test_user:t3st@db.example.invalid:25060/pmdb_test?sslmode=require"
ELSEWHERE = "postgresql://u:p@scratch.example.invalid:5432/scratch"


@pytest.fixture
def env(monkeypatch):
    """The VM's shape: prod, migrations and test DSNs all present."""
    monkeypatch.setenv("DATABASE_URL", PROD)
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", MIGRATIONS)
    monkeypatch.setenv("TEST_DATABASE_URL", TEST_DSN)


# --------------------------------------------------------------------------- #
# Labelling
# --------------------------------------------------------------------------- #


def test_database_url_labels_production(env):
    assert describe_dsn(PROD).label == PRODUCTION


def test_migrations_dsn_labels_production(env):
    """Same database, different user — string equality would say `unknown`."""
    assert describe_dsn(MIGRATIONS).label == PRODUCTION


def test_test_dsn_labels_test(env):
    assert describe_dsn(TEST_DSN).label == TEST


def test_unmatched_dsn_is_unknown_not_test(env):
    """Fail toward caution: an unrecognised target is never assumed harmless."""
    described = describe_dsn(ELSEWHERE)
    assert described.label == UNKNOWN
    assert described.label != TEST


def test_unknown_label_warns_it_may_be_production(env):
    assert "production" in describe_dsn(ELSEWHERE).label.lower()


def test_label_ignores_query_string_and_user(env, monkeypatch):
    """Same (host, port, dbname) reached with different credentials/params."""
    variant = "postgresql://someone_else:pw@db.example.invalid:25060/pmdb"
    assert describe_dsn(variant).label == PRODUCTION


def test_default_port_matches_explicit_5432(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h.invalid/db")
    monkeypatch.delenv("MIGRATIONS_DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert describe_dsn("postgresql://u:p@h.invalid:5432/db").label == PRODUCTION


def test_unparseable_dsn_is_unknown_and_unredactable(env):
    described = describe_dsn("host=db.example.invalid password=s3kr3t dbname=pmdb")
    assert described.label == UNKNOWN
    assert described.redacted is None


def test_describe_never_carries_the_password(env):
    assert "s3kr3t" not in f"{describe_dsn(PROD)}"


def test_no_env_set_labels_unknown(monkeypatch):
    for var in ("DATABASE_URL", "MIGRATIONS_DATABASE_URL", "TEST_DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    assert describe_dsn(PROD).label == UNKNOWN


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _parse(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser()
    add_dsn_args(parser)
    return parser, parser.parse_args(argv)


def test_defaults_to_database_url(env):
    parser, args = _parse([])
    assert resolve_dsn(args, parser) == PROD


def test_database_url_flag_wins(env):
    parser, args = _parse(["--database-url", ELSEWHERE])
    assert resolve_dsn(args, parser) == ELSEWHERE


def test_test_flag_resolves_test_database_url(env):
    parser, args = _parse(["--test"])
    assert resolve_dsn(args, parser) == TEST_DSN


def test_test_flag_errors_when_test_database_url_unset(monkeypatch):
    """The critical one: falling through to DATABASE_URL would be a production
    write dressed as a test write — worse than the bug #399 exists to fix."""
    monkeypatch.setenv("DATABASE_URL", PROD)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    parser, args = _parse(["--test"])
    with pytest.raises(SystemExit) as exc:
        resolve_dsn(args, parser)
    assert exc.value.code != 0


def test_test_flag_errors_when_test_database_url_is_empty(monkeypatch):
    """An empty string is 'unset' too — it must not fall through either."""
    monkeypatch.setenv("DATABASE_URL", PROD)
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    parser, args = _parse(["--test"])
    with pytest.raises(SystemExit):
        resolve_dsn(args, parser)


def test_test_and_database_url_together_is_rejected(env):
    """Two targets named at once is a mistake, not a precedence puzzle."""
    parser, args = _parse(["--test", "--database-url", ELSEWHERE])
    with pytest.raises(SystemExit):
        resolve_dsn(args, parser)


def test_missing_everything_errors(monkeypatch):
    for var in ("DATABASE_URL", "MIGRATIONS_DATABASE_URL", "TEST_DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    parser, args = _parse([])
    with pytest.raises(SystemExit):
        resolve_dsn(args, parser)


def test_resolve_echoes_the_labelled_target(env, capsys):
    parser, args = _parse([])
    resolve_dsn(args, parser)
    err = capsys.readouterr().err
    assert "pmdb" in err
    assert PRODUCTION in err
    assert "s3kr3t" not in err


def test_resolve_can_suppress_the_echo(env, capsys):
    """Multi-DSN scripts echo each target themselves with a role label."""
    parser, args = _parse([])
    resolve_dsn(args, parser, echo=False)
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------- #
# default_dsn — for scripts whose flags are domain-named
# --------------------------------------------------------------------------- #


def test_default_dsn_reads_database_url(env):
    """audit_schema_constraint_parity takes --target-url/--reference-url, so it
    cannot use add_dsn_args — but the env var name still lives in one module."""
    assert default_dsn() == PROD


def test_default_dsn_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert default_dsn() is None


def test_default_dsn_treats_empty_as_unset(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    assert default_dsn() is None
