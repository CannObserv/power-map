"""Visibility guard for the vendored skill submodules (#461 CR).

`skills-vendor/` is a git submodule, so it is **empty until someone initialises
it**. That is the default state of a freshly created worktree:
`scripts/worktree-setup.sh` builds the venv and links `.env`, and deliberately
leaves submodules alone. Tests that read vendored files therefore meet a routine
absence, not drift.

`tests/sh/claude_hooks.bats` met the same state and answered it with a
conditional `.skills/doctor.sh` heal plus a written rationale. The pytest tier
answers it the way #450 taught the repo to answer a vanished tier: skip the
tests that cannot run, then say so in red in the terminal summary. A silent skip
would let a green run overstate what it verified — which is the very shape of
bug the parity guard exists to catch, one level up.

It also owns `contains_commit`, the ancestry probe the vendored-parity guards
assert on (#463). Same subject, one level up: those guards answer "is the pin
new enough to carry this fix", and they must distinguish *no* from *could not
tell* for exactly the reason this module exists — an unanswered question
reported as an answer is the failure mode.

Keep this module dependency-free and import-safe: `tests/conftest.py` imports it
at collection time, exactly as it imports `optional_groups`.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = REPO_ROOT / "skills-vendor" / "gregoryfoster-skills"

# The probe is the file the parity guard actually reads, not the directory: an
# initialised-but-empty submodule directory exists and proves nothing.
DRIVER_PATH = VENDOR_ROOT / "skills" / "init-socraticode" / "scripts" / "mcp-driver.mjs"

INIT_HINT = "git submodule update --init skills-vendor/"

SKIP_REASON = (
    "skills-vendor/gregoryfoster-skills is uninitialised — the vendored-driver guards were NOT RUN"
)


def vendor_skills_present() -> bool:
    """True when the vendored `gregoryfoster/skills` submodule is checked out."""
    return DRIVER_PATH.is_file()


def absent_banner() -> str:
    """One-line terminal-summary banner naming the gap and the fix."""
    return f"{SKIP_REASON}; initialise with: {INIT_HINT}"


def contains_commit(rev: str, repo: Path = VENDOR_ROOT) -> bool | None:
    """Is `rev` an ancestor of the pinned submodule HEAD? `None` if git cannot say.

    Ancestry is what the vendored-parity guards assert on, because the files
    themselves are read-only here (`skills-vendor` policy) and an upstream
    reformat can move every substring without changing behaviour. Exit 0 is
    yes and exit 1 is no; anything else (128 for an unknown revision in a
    shallow clone, a missing git, a non-repo) means the question was not
    answered, which must not be reported as a rollback.

    `repo` defaults to the vendored submodule, which is what every caller in
    the suite wants; it is a parameter so the three-way answer can be exercised
    against a throwaway repo, the False branch especially — no fixture built
    from the real pin can hold still.

    Two ways the probe can end up answering about the **wrong repository**, both
    closed here, because a confident answer about the wrong repo is worse than
    no answer at all:

    - **The environment is scrubbed of `GIT_*`.** git hooks export `GIT_DIR` and
      `GIT_INDEX_FILE`, and `GIT_DIR` beats `-C`: under pre-commit — where the
      unit tier runs on every commit — an unscrubbed probe asks the superproject
      whether it contains a submodule commit, gets 128, and answers `None`.
      Every ancestry ratchet would then skip exactly where it runs most often,
      wearing the same skip message as a routine uninitialised submodule.
    - **`repo` must hold a repository of its own.** `git submodule` leaves the
      directory in place and empty until someone initialises it, and git's
      discovery walks *up* out of an empty directory — so `-C` alone answers
      about the parent checkout, returning `True` for commits only it contains.
      A missing `.git` is that state, so it answers `None` before invoking git.
    """
    if not (repo / ".git").exists():
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", rev, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(proc.returncode)
