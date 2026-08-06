"""Unit tests for the shared DSN redaction helper (#402).

Seeded by #402 for the two scripts it gates; #399 extends this module with
prod/test labelling and retrofits it across the live scripts. The invariant
that matters in both: a DSN carries a password, so nothing derived from one
reaches stdout, stderr or a journal with the password in it.
"""

import os
import subprocess
from pathlib import Path

import pytest

from scripts._dsn import echo_target, redact_dsn

DSN = "postgresql://seed_user:s3kr3t@db.example.invalid:25060/seeddb?sslmode=require"
SECRET = "s3kr3t"


def test_redacts_the_password():
    assert SECRET not in redact_dsn(DSN)


def test_keeps_the_identifying_parts():
    out = redact_dsn(DSN)
    assert "seed_user@" in out
    assert "db.example.invalid" in out
    assert "25060" in out
    assert out.endswith("/seeddb")


def test_query_string_is_dropped():
    """sslmode et al are noise, and a query string can carry credentials."""
    assert "sslmode" not in redact_dsn(DSN)


def test_dsn_without_port():
    assert redact_dsn("postgresql://u:p@host.invalid/db") == "u@host.invalid/db"


def test_dsn_without_user():
    assert redact_dsn("postgresql://host.invalid:5432/db") == "host.invalid:5432/db"


def test_dsn_without_database():
    """An absent database name renders as `?`, matching apply-schema.sh."""
    assert redact_dsn("postgresql://u:p@host.invalid:5432/") == "u@host.invalid:5432/?"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a url",
        # A libpq keyword/value DSN — urlparse would hand the password back as
        # the "path" and we would print it.
        "host=db.example.invalid user=seed_user password=s3kr3t dbname=seeddb",
        "postgresql://",
    ],
)
def test_unparseable_dsn_returns_none_rather_than_guessing(bad):
    assert redact_dsn(bad) is None


def test_unparseable_dsn_never_echoes_the_string(capsys):
    """Degrade to a placeholder — never fall back to printing the raw DSN."""
    kv = "host=db.example.invalid user=seed_user password=s3kr3t dbname=seeddb"
    echo_target(kv)
    combined = "".join(capsys.readouterr())
    assert SECRET not in combined
    assert "db.example.invalid" not in combined
    assert "cannot redact" in combined


def test_echo_target_writes_to_stderr(capsys):
    echo_target(DSN)
    captured = capsys.readouterr()
    assert "seeddb" in captured.err
    assert captured.out == "", "diagnostics belong on stderr, not in piped output"


def test_echo_target_labels_the_role(capsys):
    echo_target(DSN, role="reference")
    assert "reference:" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Parity with scripts/apply-schema.sh
# --------------------------------------------------------------------------- #

APPLY_SCHEMA = Path(__file__).parents[2] / "scripts" / "apply-schema.sh"

PARITY_DSNS = [
    DSN,
    "postgresql://u:p@host.invalid/db",
    "postgresql://host.invalid:5432/db",
    "postgresql://u:p@host.invalid:5432/",
]


def _apply_schema_target(dsn: str, tmp_path: Path) -> str:
    """Run apply-schema.sh's own redaction and return the `target:` line's payload.

    `--test --dry-run` stops after the target echo, and POWER_MAP_ENV_FILE
    redirects the DSN lookup at a throwaway file — no real database is read or
    contacted.
    """
    env_file = tmp_path / "env"
    env_file.write_text(f"TEST_DATABASE_URL={dsn}\n")
    proc = subprocess.run(
        ["bash", str(APPLY_SCHEMA), "--test", "--dry-run"],
        capture_output=True,
        text=True,
        env={**os.environ, "POWER_MAP_ENV_FILE": str(env_file), "TEST_DATABASE_URL": dsn},
    )
    assert proc.returncode == 0, proc.stderr
    for line in proc.stderr.splitlines():
        if line.startswith("target: "):
            # Strip the trailing " (test)" label — only the redaction is shared.
            return line.removeprefix("target: ").rsplit(" (", 1)[0]
    raise AssertionError(f"no target line in:\n{proc.stderr}")


@pytest.mark.parametrize("dsn", PARITY_DSNS)
def test_redaction_matches_apply_schema_sh(dsn, tmp_path):
    """The shell copy is duplicated on purpose (#398 ExecStartPre) — pin the agreement.

    apply-schema.sh cannot import this module: an import failure on the
    ExecStartPre path would be a failed production restart. Duplication is only
    safe while something checks the two copies still say the same thing.
    """
    assert _apply_schema_target(dsn, tmp_path) == redact_dsn(dsn)
