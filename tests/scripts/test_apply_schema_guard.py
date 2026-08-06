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
    """A throwaway main checkout containing the script under test.

    Deliberately *not* named ``main``: an assertion that the branch was echoed
    must not be satisfiable by the checkout path.
    """
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "apply-schema.sh")
    # A committed file the dirty-tree tests can modify without touching the
    # script they are about to run.
    (root / "README").write_text("committed\n")
    _git(root, "init", "-q", "-b", "main")
    # Fails loudly if a leaked GIT_DIR ever aims init somewhere else.
    assert (root / ".git").is_dir()
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


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

    assert "(test)" in _out(result)
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
    assert "branch=main" in out
    assert f"sha={sha}" in out


# --------------------------------------------------------------------------- #
# Warnings — never hard failures (a dirty prod checkout must still deploy)
# --------------------------------------------------------------------------- #


def test_clean_main_checkout_warns_about_nothing(repo, tmp_path):
    result = _run(repo, tmp_path, "--dry-run")

    assert "WARNING" not in _out(result)


def test_modified_tracked_file_warns_but_succeeds(repo, tmp_path):
    (repo / "README").write_text("edited\n")

    result = _run(repo, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "WARNING" in _out(result)
    assert "uncommitted" in _out(result).lower()


def test_untracked_file_does_not_warn(repo, tmp_path):
    """Untracked files cannot change schema.sql — warning on every restart is noise."""
    (repo / "scratch.txt").write_text("untracked\n")

    result = _run(repo, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "WARNING" not in _out(result)


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


def test_help_goes_to_stdout(repo, tmp_path):
    result = _run(repo, tmp_path, "--help")

    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert result.stderr == ""


def test_short_yes_alias_overrides_the_worktree_refusal(worktree, tmp_path):
    result = _run(worktree, tmp_path, "-y", "--dry-run")

    assert result.returncode == 0, _out(result)


# --------------------------------------------------------------------------- #
# DSN resolution from the env file
# --------------------------------------------------------------------------- #


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


def test_env_file_value_may_be_quoted_or_exported(repo, tmp_path):
    """uv's dotenv parser handled these; the bash fallback must too."""
    env_file = tmp_path / "quoted.env"
    env_file.write_text(f'export MIGRATIONS_DATABASE_URL="{PROD_DSN}"\n')

    result = _run(
        repo,
        tmp_path,
        "--dry-run",
        MIGRATIONS_DATABASE_URL=None,
        POWER_MAP_ENV_FILE=str(env_file),
    )

    assert result.returncode == 0, _out(result)
    assert "guard_user@guard.example.invalid:25060/guarddb" in _out(result)
    assert '"' not in _out(result)


# --------------------------------------------------------------------------- #
# Redaction must never leak, and never abort a restart (#398 CR 1, 2)
# --------------------------------------------------------------------------- #


def test_unparsable_dsn_is_never_echoed(repo, tmp_path):
    """A libpq keyword DSN has no URL structure — echoing it would leak the password."""
    result = _run(
        repo,
        tmp_path,
        "--dry-run",
        MIGRATIONS_DATABASE_URL="host=db.example password=hunter2 dbname=x",
    )

    assert result.returncode == 0, _out(result)
    assert "hunter2" not in _out(result)
    assert "cannot redact" in _out(result)


def test_missing_python3_does_not_abort(repo, tmp_path):
    """The echo is cosmetic; on ExecStartPre a non-zero exit stops the service."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    for tool in ("bash", "git", "grep", "cut", "tail", "cat"):
        found = shutil.which(tool)
        if found:
            (stub_bin / tool).symlink_to(found)

    result = _run(repo, tmp_path, "--dry-run", PATH=str(stub_bin))

    assert result.returncode == 0, _out(result)
    assert "cannot redact" in _out(result)


# --------------------------------------------------------------------------- #
# The script's own checkout is authoritative, not the caller's cwd (#398 CR 4)
# --------------------------------------------------------------------------- #


def test_worktree_script_invoked_from_main_checkout_still_refuses(repo, worktree, tmp_path):
    """The tree that owns the script is the tree whose schema.sql gets applied."""
    result = subprocess.run(
        ["bash", str(worktree / "scripts" / "apply-schema.sh"), "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=60,
    )

    assert result.returncode == 2, _out(result)
    assert str(worktree) in _out(result)


def test_non_git_directory_warns_but_proceeds(tmp_path):
    """A loose copy has no worktree guard — say so rather than pretending."""
    loose = tmp_path / "loose" / "scripts"
    loose.mkdir(parents=True)
    shutil.copy(SCRIPT, loose / "apply-schema.sh")

    result = subprocess.run(
        ["bash", "scripts/apply-schema.sh", "--dry-run"],
        cwd=loose.parent,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=60,
    )

    assert result.returncode == 0, _out(result)
    assert "not a git checkout" in _out(result)


# --------------------------------------------------------------------------- #
# An unusable git degrades; it never stops a restart (#398 CR 22)
# --------------------------------------------------------------------------- #


def _stub_git_bin(tmp_path: Path) -> Path:
    """A `git` that predates --git-common-dir (added in git 2.5)."""
    stub_bin = tmp_path / "oldgit"
    stub_bin.mkdir()
    (stub_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do\n'
        '  [ "$a" = "--git-common-dir" ] && { echo "error: unknown option" >&2; exit 129; }\n'
        "done\n"
        f'exec {shutil.which("git")} "$@"\n'
    )
    (stub_bin / "git").chmod(0o755)
    for tool in ("bash", "grep", "cut", "tail", "cat", "python3"):
        found = shutil.which(tool)
        if found:
            (stub_bin / tool).symlink_to(found)
    return stub_bin


def test_git_without_common_dir_does_not_refuse(repo, tmp_path):
    """`cd ""` succeeds silently, so a blank common dir must never imply a worktree."""
    result = _run(repo, tmp_path, "--dry-run", PATH=str(_stub_git_bin(tmp_path)))

    assert result.returncode == 0, _out(result)
    assert "worktree guard is unavailable" in _out(result)
    assert "refusing" not in _out(result)


def test_commitless_repo_does_not_abort(tmp_path):
    """rev-parse HEAD fails without commits; set -e would end the restart."""
    root = tmp_path / "fresh"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "apply-schema.sh")
    _git(root, "init", "-q", "-b", "main")

    result = _run(root, tmp_path, "--dry-run")

    assert result.returncode == 0, _out(result)
    assert "schema applied" not in _out(result)


# --------------------------------------------------------------------------- #
# Interactive confirmation (TTY only — systemd never sees this path)
# --------------------------------------------------------------------------- #


def _run_on_tty(
    cwd: Path,
    tmp_path: Path,
    reply: bytes | None,
    *args: str,
    **over: str | None,
) -> tuple[int, str]:
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", "scripts/apply-schema.sh", *args],
        cwd=cwd,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_env(tmp_path, **over),
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


def test_confirmation_prompt_names_only_the_database(repo, tmp_path):
    """The token must be the database name, not the whole target description."""
    code, out = _run_on_tty(repo, tmp_path, b"guarddb\n", "--dry-run")

    assert code == 0, out
    assert "(guarddb)" in out


def test_confirmation_falls_back_when_the_dsn_has_no_database(repo, tmp_path):
    """A pathless DSN yields no database name — ask for a fixed word instead."""
    code, out = _run_on_tty(
        repo,
        tmp_path,
        b"production\n",
        "--dry-run",
        MIGRATIONS_DATABASE_URL="postgresql://guard_user:s3kr3t-prod@guard.example.invalid:25060",
    )

    assert code == 0, out
    assert "(production)" in out
    assert SECRET not in out


def test_confirmation_falls_back_when_the_dsn_is_unparsable(repo, tmp_path):
    code, out = _run_on_tty(
        repo,
        tmp_path,
        b"production\n",
        "--dry-run",
        MIGRATIONS_DATABASE_URL="host=db.example password=hunter2 dbname=x",
    )

    assert code == 0, out
    assert "(production)" in out
    assert "hunter2" not in out
