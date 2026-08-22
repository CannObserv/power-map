"""Guards the vendored-submodule visibility helper (#461 CR).

`tests/vendor_skills.py` exists so a suite that cannot see `skills-vendor/`
*says so* instead of skipping quietly. The state is routine, not exotic: a
freshly created worktree has uninitialized submodules until someone runs
`git submodule update --init` (`scripts/worktree-setup.sh` does the venv and
`.env`, not the submodules), and `tests/sh/claude_hooks.bats` already carries a
written rationale for meeting the same state next door.

The failure this prevents is the one #450 taught the repo with dependency
groups: a tier that vanishes reads as a few skips against an otherwise green
run, and the green overstates what was verified. Same shape, same answer — skip
the tests, then name the gap in red in the terminal summary.
"""

import os
import subprocess
from pathlib import Path

import pytest

from tests import vendor_skills

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_driver_path_points_into_the_vendored_skill() -> None:
    """The probe targets `mcp-driver.mjs`, the file the parity guard reads."""
    rel = vendor_skills.DRIVER_PATH.relative_to(REPO_ROOT).as_posix()
    assert rel == (
        "skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/mcp-driver.mjs"
    ), f"probe points at {rel}, not the vendored driver"


def test_present_is_true_when_the_submodule_is_initialised() -> None:
    """In a working checkout the probe resolves, so nothing is skipped."""
    assert vendor_skills.DRIVER_PATH.is_file(), (
        "the vendored driver is missing — this test asserts the happy path, so "
        "run `git submodule update --init skills-vendor/` first"
    )
    assert vendor_skills.vendor_skills_present() is True


def test_absent_banner_names_the_gap_and_the_fix() -> None:
    """The banner says the tests did NOT run, and how to make them run."""
    banner = vendor_skills.absent_banner()

    assert "NOT RUN" in banner, (
        "the banner must say the tests did not run — a skip that reads as a "
        "pass is the whole failure mode this exists to prevent"
    )
    assert vendor_skills.INIT_HINT in banner, "the banner must carry the init command"
    assert "git submodule update --init" in vendor_skills.INIT_HINT


def test_skip_reason_is_the_same_sentence_as_the_banner() -> None:
    """One wording, whether it surfaces via `-rs` or the terminal summary."""
    assert vendor_skills.SKIP_REASON in vendor_skills.absent_banner()


def _git(repo: Path, *args: str) -> str:
    """Run git in `repo` with a hermetic environment, returning stdout.

    `GIT_DIR` / `GIT_INDEX_FILE` / `GIT_WORK_TREE` are set when the suite runs
    under a pre-commit hook and point at the *real* repo, so a fixture that
    inherits them commits into it instead. `tests/sh/claude_hooks.bats` carries
    the same precaution for the same reason.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["HOME"] = str(repo)
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo whose HEAD contains `first` and does not contain `orphan`."""
    repo = tmp_path / "pin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")

    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "first")
    first = _git(repo, "rev-parse", "HEAD")

    # A commit on a side branch, never merged: a real revision HEAD does not
    # contain, which is the state a submodule rollback produces.
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "b.txt").write_text("two\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "orphan")
    orphan = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")

    return repo, first, orphan


def test_contains_commit_defaults_to_the_vendored_submodule() -> None:
    """No-argument callers ask about `skills-vendor/gregoryfoster-skills`.

    Both ancestry ratchets call it that way, so the default is their whole
    contract: pointed anywhere else, every one of them would answer about the
    wrong repository while still going green.
    """
    assert vendor_skills.contains_commit("HEAD") is True


def test_contains_commit_is_true_for_an_ancestor(two_commit_repo: tuple[Path, str, str]) -> None:
    """The commit is in HEAD's history — the ratchet holds."""
    repo, first, _ = two_commit_repo
    assert vendor_skills.contains_commit(first, repo=repo) is True


def test_contains_commit_is_false_for_a_commit_head_lacks(
    two_commit_repo: tuple[Path, str, str],
) -> None:
    """A real revision outside HEAD's history — what a rollback looks like."""
    repo, _, orphan = two_commit_repo
    assert vendor_skills.contains_commit(orphan, repo=repo) is False


def test_an_unresolvable_revision_is_unknown_not_a_rollback(
    two_commit_repo: tuple[Path, str, str],
) -> None:
    """git exits 128, which must read as "not answered", never as "absent".

    A shallow clone cannot resolve an old commit, and reporting that as a
    rollback would send the reader to bump a submodule that is already correct
    — the confidently-wrong signal the parity guards are written to avoid.
    """
    repo, _, _ = two_commit_repo
    assert vendor_skills.contains_commit("0" * 40, repo=repo) is None


def test_a_directory_that_is_not_a_repo_is_unknown(tmp_path: Path) -> None:
    """The same tri-state answer when the submodule is uninitialised, not shallow."""
    assert vendor_skills.contains_commit("HEAD", repo=tmp_path) is None


def test_an_inherited_git_dir_cannot_redirect_the_question(
    two_commit_repo: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GIT_DIR` in the environment must not silently retarget the probe.

    pre-commit exports `GIT_DIR` and `GIT_INDEX_FILE` pointing at the *real*
    repo, and `git -C <path>` loses to `GIT_DIR`. Unscrubbed, every ancestry
    ratchet asks the superproject whether it contains a submodule commit, gets
    128 for an unknown revision, and reads as "cannot tell" — so the whole
    guard skips in the one place it runs on every commit, and the skip looks
    like the routine uninitialised-submodule one.
    """
    repo, first, _ = two_commit_repo
    monkeypatch.setenv("GIT_DIR", str(REPO_ROOT / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(REPO_ROOT / ".git" / "index"))

    assert vendor_skills.contains_commit(first, repo=repo) is True
