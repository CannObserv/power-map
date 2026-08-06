"""Guards on scripts/apply-schema.sh (#398).

The script targets **production** (`MIGRATIONS_DATABASE_URL`) and is wired as
``ExecStartPre`` on the systemd unit, so every hard-fail guard here must be one
the unit's shape (main checkout, no TTY, no flags) can never trip — otherwise a
guard bug becomes a failed prod restart.

Each test builds a throwaway git repo in ``tmp_path`` holding a copy of the
script, so the assertions never depend on this checkout's branch or cleanliness
and never see the real DSNs.
"""

import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "apply-schema.sh"

PROD_DSN = "postgresql://guard_user:s3kr3t-prod@guard.example.invalid:25060/guarddb?sslmode=require"
TEST_DSN = (
    "postgresql://guard_user:s3kr3t-test@guard.example.invalid:25060/guarddb_test?sslmode=require"
)
SECRET = "s3kr3t"


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> None:
    """Run git against *cwd* only.

    ``GIT_DIR`` / ``GIT_INDEX_FILE`` are stripped: git exports them when it
    invokes a hook, so a suite run from pre-commit would otherwise aim these
    throwaway-repo commands at the real repository.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        ["git", "-c", "user.email=guard@example.invalid", "-c", "user.name=Guard Test", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway main checkout containing the script under test."""
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, main / "scripts" / "apply-schema.sh")
    _git(main, "init", "-q", "-b", "main")
    # Fails loudly if a leaked GIT_DIR ever aims init somewhere else.
    assert (main / ".git").is_dir()
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "init")
    return main


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    """A linked worktree of *repo* — the shape that caused the #392 incident."""
    path = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "feat/guard", str(path))
    return path


def _env(tmp_path: Path, **overrides: str | None) -> dict[str, str]:
    """Minimal environment — never inherits the real DSNs."""
    env: dict[str, str | None] = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        # Point the .env fallback at a path that does not exist so the real
        # /etc/power-map/.env can never leak a production DSN into a test.
        "POWER_MAP_ENV_FILE": str(tmp_path / "absent.env"),
        "MIGRATIONS_DATABASE_URL": PROD_DSN,
        "TEST_DATABASE_URL": TEST_DSN,
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def _run(cwd: Path, tmp_path: Path, *args: str, **over: str | None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/apply-schema.sh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_env(tmp_path, **over),
        timeout=60,
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# Worktree refusal — the #392 case
# --------------------------------------------------------------------------- #


def test_prod_from_linked_worktree_refuses(worktree, tmp_path):
    """Exit 2 before any connection, naming production and the safe door."""
    result = _run(worktree, tmp_path)

    assert result.returncode == 2
    out = _out(result)
    assert "PRODUCTION" in out
    assert "--test" in out
    assert "schema applied" not in out


def test_worktree_refusal_names_the_target_database(worktree, tmp_path):
    result = _run(worktree, tmp_path)

    assert "guarddb" in _out(result)


def test_yes_overrides_the_worktree_refusal(worktree, tmp_path):
    result = _run(worktree, tmp_path, "--yes", "--dry-run")

    assert result.returncode == 0, _out(result)


def test_main_checkout_does_not_refuse(repo, tmp_path):
    """The systemd shape — main checkout, no TTY, no flags — must pass."""
    result = _run(repo, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "guarddb" in _out(result)


# --------------------------------------------------------------------------- #
# --test: the safe door
# --------------------------------------------------------------------------- #


def test_test_target_allowed_from_worktree(worktree, tmp_path):
    result = _run(worktree, tmp_path, "--test", "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "guarddb_test" in _out(result)


def test_test_target_is_labelled_not_production(worktree, tmp_path):
    result = _run(worktree, tmp_path, "--test", "--dry-run")

    assert "test" in _out(result).lower()
    assert "PRODUCTION" not in _out(result)


def test_test_target_missing_dsn_exits_1(worktree, tmp_path):
    result = _run(worktree, tmp_path, "--test", "--dry-run", TEST_DATABASE_URL=None)

    assert result.returncode == 1
    assert "TEST_DATABASE_URL" in _out(result)


# --------------------------------------------------------------------------- #
# Target echo
# --------------------------------------------------------------------------- #


def test_target_echo_redacts_the_password(repo, tmp_path):
    result = _run(repo, tmp_path, "--dry-run")

    assert SECRET not in _out(result)
    assert "guard_user" in _out(result)
    assert "guard.example.invalid" in _out(result)


def test_target_echo_reports_branch_and_sha(repo, tmp_path):
    result = _run(repo, tmp_path, "--dry-run")
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    out = _out(result)
    assert "main" in out
    assert sha in out


# --------------------------------------------------------------------------- #
# Warnings — never hard failures (a dirty prod checkout must still deploy)
# --------------------------------------------------------------------------- #


def test_clean_main_checkout_warns_about_nothing(repo, tmp_path):
    result = _run(repo, tmp_path, "--dry-run")

    assert "WARNING" not in _out(result)


def test_dirty_checkout_warns_but_succeeds(repo, tmp_path):
    (repo / "scratch.txt").write_text("uncommitted\n")

    result = _run(repo, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "WARNING" in _out(result)
    assert "uncommitted" in _out(result).lower()


def test_non_default_branch_warns_but_succeeds(repo, tmp_path):
    _git(repo, "switch", "-q", "-c", "feat/hotfix")

    result = _run(repo, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "WARNING" in _out(result)
    assert "feat/hotfix" in _out(result)


# --------------------------------------------------------------------------- #
# Config / usage errors
# --------------------------------------------------------------------------- #


def test_missing_prod_dsn_exits_1(repo, tmp_path):
    result = _run(repo, tmp_path, "--dry-run", MIGRATIONS_DATABASE_URL=None)

    assert result.returncode == 1
    assert "MIGRATIONS_DATABASE_URL" in _out(result)


def test_unknown_flag_exits_1(repo, tmp_path):
    result = _run(repo, tmp_path, "--nope")

    assert result.returncode == 1
    assert "usage" in _out(result).lower()


def test_env_file_supplies_the_dsn_when_unset(repo, tmp_path):
    env_file = tmp_path / "present.env"
    env_file.write_text(f"OTHER=1\nMIGRATIONS_DATABASE_URL={PROD_DSN}\n")

    result = _run(
        repo,
        tmp_path,
        "--dry-run",
        MIGRATIONS_DATABASE_URL=None,
        POWER_MAP_ENV_FILE=str(env_file),
    )

    assert result.returncode == 0, _out(result)
    assert "guarddb" in _out(result)


# --------------------------------------------------------------------------- #
# Interactive confirmation (TTY only — systemd never sees this path)
# --------------------------------------------------------------------------- #


def _run_on_tty(cwd: Path, tmp_path: Path, reply: bytes | None, *args: str) -> tuple[int, str]:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", "scripts/apply-schema.sh", *args],
        cwd=cwd,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_env(tmp_path),
    )
    os.close(slave)
    try:
        if reply is not None:
            os.write(master, reply)
        out, _ = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("script blocked on input it should not have asked for")
    finally:
        os.close(master)
    return proc.returncode, out


def test_interactive_prod_run_requires_confirmation(repo, tmp_path):
    code, out = _run_on_tty(repo, tmp_path, b"guarddb\n", "--dry-run")

    assert code == 0, out
    assert "guarddb" in out


def test_interactive_prod_run_aborts_on_wrong_answer(repo, tmp_path):
    code, out = _run_on_tty(repo, tmp_path, b"no\n", "--dry-run")

    assert code == 2
    assert "schema applied" not in out


def test_interactive_yes_skips_confirmation(repo, tmp_path):
    code, out = _run_on_tty(repo, tmp_path, None, "--yes", "--dry-run")

    assert code == 0, out


def test_interactive_test_target_never_prompts(repo, tmp_path):
    code, out = _run_on_tty(repo, tmp_path, None, "--test", "--dry-run")

    assert code == 0, out
    assert "guarddb_test" in out
